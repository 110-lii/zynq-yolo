#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板上验收：球链路三组参数对照（默认 / 丢失窗口按秒 / 预测越界）。

目的：板子插上就能一条命令跑完「一次只验一个开关」，给出可回查的对照表。

板上运行：
    python3 tools/check_board_acceptance.py --repo /home/xilinx/zynq-yolo \
        --video /home/xilinx/clips.mp4 --calib data/calib/field_calib.json

PC 上验编排与统计（不跑管线）：
    py tools/check_board_acceptance.py --replay data/results/run_xxx.json
    py tools/check_board_acceptance.py --selftest

判据（末行令牌）：
    BOARD_ACCEPTANCE_OK
    BOARD_ACCEPTANCE_FAIL <原因>

* base 组必须达到 --baseline（默认 144 球帧 / 49 球权，取自 docs/system_status.md
  的稳定片段）；给 --baseline none 时只记录、不判定。
* lostsec / predict 组相对 base：球场坐标球帧与球权帧都不得下降超过 --tol（默认 5%）。
  下降超限即 FAIL —— 这正是 session 010 两次回滚的判据。
* 越界预测会新增 events，**事件增加不算通过**，只提示，由人工复核真值。
"""
import argparse
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True

GROUPS = [
    ("base", [], "稳定版参数，两个新开关都不开"),
    ("lostsec", ["--ball-lost-sec", "0.4"], "丢失窗口按秒计（0.4 s ≈ 原 5 帧@12fps）"),
    ("predict", ["--ball-predict-past-boundary"], "预测点允许越过球场 ±5 m"),
]
KEYS = ('frames', 'ball_any', 'ball_field', 'possession', 'out_of_play', 'goal', 'predicted')


def summarize(path):
    """统计一份 board_pipeline 输出 JSON 的关键帧数。"""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    frames = data.get('frames') if isinstance(data, dict) else data
    if frames is None:
        raise ValueError('输出 JSON 里找不到 frames 数组')
    n = len(frames)
    ball_any = ball_field = possess = oop = goal = predicted = 0
    for fr in frames:
        b = fr.get('ball') or {}
        if b.get('valid'):
            ball_any += 1
            if b.get('space', 'field') == 'field':
                ball_field += 1
            if b.get('predicted'):
                predicted += 1
        ev = fr.get('events') or {}
        if ev.get('possession') is not None:
            possess += 1
        if ev.get('out_of_play'):
            oop += 1
        if ev.get('goal'):
            goal += 1
    return {'frames': n, 'ball_any': ball_any, 'ball_field': ball_field,
            'possession': possess, 'out_of_play': oop, 'goal': goal,
            'predicted': predicted}


def run_pipeline(repo, video, calib, every, ball_every, extra, out_path, timeout):
    cmd = [sys.executable, os.path.join(repo, 'tools', 'board_pipeline.py'),
           '--video', video, '--every', str(every), '--ball-every', str(ball_every),
           '--max-det', '0', '--proposal', '--out', out_path]
    if calib:
        cmd += ['--calib', calib]
    cmd += extra
    print('  $ ' + ' '.join(cmd))
    try:
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f'超时 {timeout}s'
    tail = (r.stdout or '')[-400:].strip().replace('\n', ' | ')
    print(f'  rc={r.returncode}  {tail}')
    if r.returncode != 0:
        err = (r.stderr or '')[-300:].strip().replace('\n', ' | ')
        return False, f'退出码 {r.returncode}: {err or tail}'
    if not os.path.exists(out_path):
        return False, f'没有产出 {out_path}'
    return True, ''


def pct_drop(base, cur):
    if not base:
        return None
    return (base - cur) / base * 100.0


def pct_change(base, cur):
    """显示用：正数=比 base 提升，负数=下降。"""
    if not base:
        return None
    return (cur - base) / base * 100.0


def print_and_judge(results, base_ball, base_poss, tol):
    """打印对照表并判定，返回 (fails, notes)。"""
    fails, notes = [], []
    if not isinstance(results, dict) or not results.get('base'):
        return ['base 组没有有效结果，后面无法对照'], notes

    base = results['base']
    print('\n== 对照表（相对 base）')
    hdr = f'{"组":10s}{"球帧":>7s}{"球权":>7s}{"越界事件":>9s}{"预测帧":>8s}{"球帧Δ":>9s}{"球权Δ":>9s}'
    print(hdr)
    print('-' * 60)
    for name in ('base', 'lostsec', 'predict'):
        if name not in results:
            print(f'{name:10s}{"—":>7s}')
            continue
        s = results[name]
        cb = pct_change(base['ball_field'], s['ball_field'])
        cp = pct_change(base['possession'], s['possession'])
        fb = '—' if cb is None else f'{cb:+.1f}%'
        fp = '—' if cp is None else f'{cp:+.1f}%'
        print(f'{name:10s}{s["ball_field"]:7d}{s["possession"]:7d}'
              f'{s["out_of_play"]:9d}{s["predicted"]:8d}{fb:>9s}{fp:>9s}')

    if base_ball is not None:
        if base['ball_field'] < base_ball:
            fails.append(f'base 球帧 {base["ball_field"]} < 基线 {base_ball}（环境或代码退化）')
        if base_poss is not None and base['possession'] < base_poss:
            fails.append(f'base 球权 {base["possession"]} < 基线 {base_poss}')

    for name in ('lostsec', 'predict'):
        if name not in results:
            continue
        s = results[name]
        db = pct_drop(base['ball_field'], s['ball_field'])
        dp = pct_drop(base['possession'], s['possession'])
        if db is not None and db > tol:
            fails.append(f'{name} 球帧下降 {db:.1f}% > {tol}%')
        if dp is not None and dp > tol:
            fails.append(f'{name} 球权下降 {dp:.1f}% > {tol}%')
        if s['out_of_play'] > base['out_of_play']:
            notes.append(f'{name} 越界事件 {s["out_of_play"]} > base {base["out_of_play"]}'
                         f'：新增事件来自越界预测，必须人工复核真值，不算通过')
    return fails, notes


def selftest():
    """不依赖板子：构造三组假结果，走完整对照与判定逻辑。"""
    def mk(ball, poss, oop, pred):
        return {'frames': 202, 'ball_any': ball, 'ball_field': ball,
                'possession': poss, 'out_of_play': oop, 'goal': 0, 'predicted': pred}

    print('== selftest 1/3：全部达标应 OK')
    ok = {'base': mk(144, 49, 0, 0), 'lostsec': mk(150, 52, 0, 0), 'predict': mk(150, 50, 6, 6)}
    fails, _ = print_and_judge(ok, 144, 49, 5.0)
    if fails:
        print('SELFTEST_FAIL 场景1 应通过却报错:', fails)
        return 1

    print('\n== selftest 2/3：base 低于基线应 FAIL')
    bad = {'base': mk(120, 40, 0, 0), 'lostsec': mk(121, 41, 0, 0), 'predict': mk(122, 41, 0, 0)}
    fails, _ = print_and_judge(bad, 144, 49, 5.0)
    if not any('base 球帧' in f for f in fails):
        print('SELFTEST_FAIL 场景2 未报 base 球帧不足:', fails)
        return 1

    print('\n== selftest 3/3：实验组球权掉过头应 FAIL')
    drop = {'base': mk(144, 49, 0, 0), 'lostsec': mk(144, 30, 0, 0), 'predict': mk(146, 49, 2, 2)}
    fails, _ = print_and_judge(drop, 144, 49, 5.0)
    if not any('lostsec 球权下降' in f for f in fails):
        print('SELFTEST_FAIL 场景3 未报球权下降:', fails)
        return 1

    print('\nSELFTEST_OK 三个场景判定都符合预期')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='.')
    ap.add_argument('--video', help='板上视频路径（跑管线时必填）')
    ap.add_argument('--calib', default='')
    ap.add_argument('--every', type=int, default=25)
    ap.add_argument('--ball-every', type=int, default=2)
    ap.add_argument('--outdir', default='/tmp/acceptance')
    ap.add_argument('--timeout', type=int, default=1800, help='单组超时秒数')
    ap.add_argument('--baseline', default='144/49',
                    help='稳定版基线「球帧/球权」；给 none 表示只记录不判定')
    ap.add_argument('--tol', type=float, default=5.0, help='允许下降百分比')
    ap.add_argument('--replay', action='append', default=[],
                    help='PC 端：直接统计已有 JSON（可重复给多份）')
    ap.add_argument('--selftest', action='store_true',
                    help='不跑管线也不读数据：验证判定逻辑本身')
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.replay:
        print('== replay 模式（不跑管线，只统计）')
        for p in args.replay:
            try:
                s = summarize(p)
            except Exception as e:
                print(f'BOARD_ACCEPTANCE_FAIL 解析失败 {p}: {e}')
                return 1
            print('  ' + os.path.basename(p) + '  ' + '  '.join(f'{k}={s[k]}' for k in KEYS))
        print('BOARD_ACCEPTANCE_OK')
        return 0

    if not args.video:
        print('BOARD_ACCEPTANCE_FAIL 没给 --video（或改用 --replay / --selftest）')
        return 2

    repo = os.path.abspath(args.repo)
    os.makedirs(args.outdir, exist_ok=True)

    if str(args.baseline).lower() in ('none', '0/0', ''):
        base_ball = base_poss = None
        print('== 基线：未启用，本次只记录不判定')
    else:
        try:
            bb, bp = str(args.baseline).split('/')
            base_ball, base_poss = int(bb), int(bp)
        except Exception:
            print(f'BOARD_ACCEPTANCE_FAIL --baseline 格式应为 球帧/球权，收到 {args.baseline}')
            return 2
        print(f'== 基线：球场坐标球帧 >= {base_ball}，球权帧 >= {base_poss}')

    results = {}
    fails = []
    for name, extra, desc in GROUPS:
        out = os.path.join(args.outdir, f'{name}.json')
        print(f'\n-- {name}: {desc}')
        ok, why = run_pipeline(repo, args.video, args.calib, args.every,
                               args.ball_every, extra, out, args.timeout)
        if not ok:
            fails.append(f'{name} 跑不动（{why}）')
            continue
        try:
            results[name] = summarize(out)
        except Exception as e:
            fails.append(f'{name} 输出解析失败（{e}）')
            continue
        print('  → ' + '  '.join(f'{k}={results[name][k]}' for k in KEYS))

    jfails, notes = print_and_judge(results, base_ball, base_poss, args.tol)
    for n in notes:
        print(f'  ! {n}')
    allfails = fails + jfails
    print()
    if allfails:
        for f in allfails:
            print('  ·', f)
        print('BOARD_ACCEPTANCE_FAIL ' + '；'.join(allfails))
        return 1
    print('BOARD_ACCEPTANCE_OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())