#!/usr/bin/env node
/**
 * DSH（DeepSeek Harness）会话库解压助手 —— `export_transcript_dsh.py` 的配套脚本。
 *
 * 为什么必须用 Node，而不是 Python
 * --------------------------------
 * DSH 的会话原文存在 `~/.dsh/sessions/<工作区键>/<会话 ID>/session.v4.jsonl.zstd`，
 * 是 **zstd 压缩 + 逐次追加（每次追加一个独立 zstd 帧）** 的 JSONL。
 * 本机实测（2026-10-18）：
 *   · Python 3.13.1（`D:\python\python.exe`）**没有** `zstandard` 包；
 *     标准库 `compression.zstd` 只在 3.14+ 才有，本机 `import compression.zstd` 失败；
 *   · Node v24.18.0（`C:\Program Files\nodejs\node.exe`）**自带** `zlib.zstdDecompressSync`。
 * ⇒ 用 Node 解压、Python 解析（两段式）。**不装任何第三方依赖**（红线：禁止 pip/npm install）。
 *
 * 两个坑（实测，别改回去）
 * ------------------------
 * 1. **文件是多帧拼接的**：一次工具调用/一步 = 一帧。`zstdDecompressSync` 与
 *    `createZstdDecompress()` 都只解**第一帧**就收工 —— 直接解会只拿到 291 字节的
 *    `{"type":"session",...}` 然后把整个会话判成"空"。故本脚本先按 zstd 帧格式
 *    手动切帧（magic + frame header + 逐 block 累加长度），再**逐帧**解压拼接。
 * 2. **文件在会话进行中仍被追加**：最后一帧可能只写了一半。故某帧解压失败时
 *    **不报错退出**，而是记 `truncatedTail: true` 并把已解出的部分原样交出 ——
 *    否则正在进行的会话永远导不出来。
 *
 * 用法
 * ----
 *     node dsh_session_dump.mjs --list <sessions 根目录>
 *         逐个会话只解**第一帧**（含 session 元数据：id / cwd / createdAt / origin …），
 *         以 JSON 数组打到 stdout。快，用来做"选哪个会话"。
 *
 *     node dsh_session_dump.mjs --dump <in.zstd> <out.jsonl>
 *         完整解压成纯文本 JSONL，并往 stdout 打一行 JSON 统计（帧数 / 字节数 / 是否截尾）。
 *
 * 退出码：0 正常；2 参数错误或文件不可读。
 */

import { readFileSync, readdirSync, existsSync, writeFileSync, statSync } from 'node:fs';
import { zstdDecompressSync } from 'node:zlib';
import { join } from 'node:path';

const SESSION_FILE = 'session.v4.jsonl.zstd';
const ZSTD_MAGIC = 0xfd2fb528;
const SKIPPABLE_MASK = 0xfffffff0;
const SKIPPABLE_MAGIC = 0x184d2a50;

/**
 * 按 zstd 帧格式切帧。返回 [start, end, isSkippable] 列表。
 * 帧结构（RFC 8878）：Magic(4) + Frame_Header_Descriptor(1) + [Window_Descriptor]
 *   + [Dictionary_ID] + [Frame_Content_Size] + Blocks… + [Content_Checksum(4)]
 */
function splitFrames(buf) {
  const frames = [];
  let off = 0;
  while (off + 4 <= buf.length) {
    const magic = buf.readUInt32LE(off);
    if (magic === ZSTD_MAGIC) {
      let p = off + 4;
      const fhd = buf[p];
      p += 1;
      const fcsFlag = fhd >> 6;          // bits 7-6
      const singleSegment = (fhd >> 5) & 1; // bit 5
      const checksumFlag = (fhd >> 2) & 1;  // bit 2
      const didFlag = fhd & 3;              // bits 1-0
      if (!singleSegment) p += 1;           // Window_Descriptor
      p += [0, 1, 2, 4][didFlag];           // Dictionary_ID
      p += fcsFlag === 0 ? (singleSegment ? 1 : 0) : [0, 2, 4, 8][fcsFlag];
      for (;;) {
        if (p + 3 > buf.length) throw new Error('truncated block header at ' + p);
        const bh = buf.readUIntLE(p, 3);
        p += 3;
        const last = bh & 1;
        const blockType = (bh >>> 1) & 3;
        const blockSize = bh >>> 3;
        p += blockType === 1 ? 1 : blockSize; // RLE 块存 1 字节
        if (p > buf.length) throw new Error('truncated block payload at ' + p);
        if (last) break;
      }
      if (checksumFlag) p += 4;
      if (p > buf.length) throw new Error('truncated frame at ' + p);
      frames.push([off, p, false]);
      off = p;
    } else if ((magic & SKIPPABLE_MASK) === SKIPPABLE_MAGIC) {
      if (off + 8 > buf.length) break;
      const size = buf.readUInt32LE(off + 4);
      frames.push([off, off + 8 + size, true]);
      off = off + 8 + size;
    } else if (magic === 0) {
      break; // 尾部零填充
    } else {
      throw new Error('bad zstd magic 0x' + magic.toString(16) + ' at ' + off);
    }
  }
  return frames;
}

/** 解整份会话文件；尾部未写完的帧不致命。 */
function decodeFile(path) {
  const buf = readFileSync(path);
  const frames = splitFrames(buf);
  const parts = [];
  let decodedFrames = 0;
  let truncatedTail = false;
  for (const [a, b, skip] of frames) {
    if (skip) continue;
    try {
      parts.push(zstdDecompressSync(buf.subarray(a, b)).toString('utf8'));
      decodedFrames += 1;
    } catch (e) {
      // 会话正在被追加：最后一帧可能还没写完整 —— 保留已解出的部分。
      truncatedTail = true;
      break;
    }
  }
  return { text: parts.join(''), frames: frames.length, decodedFrames, truncatedTail, bytes: buf.length };
}

/**
 * 只解**前几帧**拿会话元数据（--list 用，不解整份）：
 *   · `session` 记录        → id / cwd / createdAt / origin / parentSession …
 *   · `subagent/descriptor` → label（父 agent 给这个子 agent 的角色说明，第 2 帧）
 *   · 首条 `user/message`   → 提示词开头（判断"这个会话在干什么"，给 --list 检索用）
 */
function peekMeta(path) {
  const buf = readFileSync(path);
  const frames = splitFrames(buf).filter((f) => !f[2]);
  if (!frames.length) return null;
  let meta = null;
  let label = null;
  let firstPrompt = '';
  for (const [a, b] of frames.slice(0, 6)) {
    let text;
    try {
      text = zstdDecompressSync(buf.subarray(a, b)).toString('utf8');
    } catch {
      break;
    }
    for (const line of text.split('\n')) {
      if (!line.trim()) continue;
      let rec;
      try {
        rec = JSON.parse(line);
      } catch {
        continue;
      }
      if (rec.type === 'session' && !meta) meta = rec;
      else if (rec.type === 'subagent/descriptor' && !label) {
        label = (rec.data && rec.data.label) || null;
      } else if (!firstPrompt && rec.type === 'user/message') {
        const content = (rec.data && rec.data.content) || [];
        firstPrompt = content
          .map((c) => (c.type === 'text' ? c.text : '[' + c.type + ']'))
          .join(' ')
          .slice(0, 120);
      }
    }
    if (meta && label && firstPrompt) break;
  }
  if (!meta) return null;
  meta.label = label;
  meta.firstPrompt = firstPrompt;
  return meta;
}

/**
 * 找出所有会话目录。两种布局都要支持：
 *   <root>/<工作区键>/<会话 ID>/session.v4.jsonl.zstd   ← `~/.dsh/sessions` 根（默认）
 *   <root>/<会话 ID>/session.v4.jsonl.zstd              ← 直接指向某个工作区键目录
 * 返回 [{workspace, dir, file}]
 */
function findSessionDirs(root) {
  const out = [];
  for (const entry of readdirSync(root)) {
    const p = join(root, entry);
    if (!statSync(p).isDirectory()) continue;
    if (existsSync(join(p, SESSION_FILE))) {
      out.push({ workspace: '', dir: entry, file: join(p, SESSION_FILE) });
      continue;
    }
    for (const sub of readdirSync(p)) {
      const q = join(p, sub);
      if (statSync(q).isDirectory() && existsSync(join(q, SESSION_FILE))) {
        out.push({ workspace: entry, dir: sub, file: join(q, SESSION_FILE) });
      }
    }
  }
  return out;
}

function main(argv) {
  const mode = argv[2];
  if (mode === '--list') {
    const root = argv[3];
    if (!root || !existsSync(root)) {
      console.error('sessions 根目录不存在：' + root);
      return 2;
    }
    const out = [];
    for (const { workspace, dir, file } of findSessionDirs(root)) {
      try {
        const meta = peekMeta(file);
        out.push({
          workspace,
          dir,
          file,
          sessionId: meta && meta.id ? meta.id : dir,
          cwd: meta ? meta.cwd : '',
          createdAt: meta ? meta.createdAt : null,
          origin: meta ? meta.origin || 'primary' : 'unknown',
          parentSession: meta ? meta.parentSession || null : null,
          delegationDepth: meta ? meta.delegationDepth || 0 : 0,
          agentPreset: meta ? meta.agentPreset || '' : '',
          version: meta ? meta.version || 0 : 0,
          label: (meta && meta.label) || '',
          firstPrompt: (meta && meta.firstPrompt) || '',
          bytes: statSync(file).size,
        });
      } catch (e) {
        out.push({ workspace, dir, file, error: String(e && e.message ? e.message : e) });
      }
    }
    process.stdout.write(JSON.stringify(out, null, 1) + '\n');
    return 0;
  }

  if (mode === '--dump') {
    const inPath = argv[3];
    const outPath = argv[4];
    if (!inPath || !outPath) {
      console.error('用法：node dsh_session_dump.mjs --dump <in.zstd> <out.jsonl>');
      return 2;
    }
    if (!existsSync(inPath)) {
      console.error('找不到会话文件：' + inPath);
      return 2;
    }
    const res = decodeFile(inPath);
    writeFileSync(outPath, res.text, 'utf8');
    process.stdout.write(JSON.stringify({
      input: inPath, output: outPath, frames: res.frames, decodedFrames: res.decodedFrames,
      truncatedTail: res.truncatedTail, compressedBytes: res.bytes, decodedChars: res.text.length,
    }) + '\n');
    return 0;
  }

  console.error('用法：node dsh_session_dump.mjs --list <root> | --dump <in.zstd> <out.jsonl>');
  return 2;
}

process.exit(main(process.argv));
