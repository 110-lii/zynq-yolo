"""board_jupyter.py -- 通过板端 Jupyter API 执行命令/传文件（SSH 不可用时的通道）。

板子上的 PYNQ Jupyter 用默认密码 xilinx，且 /api/kernels 需要登录 cookie；
本工具处理登录、_xsrf、websocket 与 base64 上传，供板端实验复用。

用法：
    python tools/board_jupyter.py "python3 -c 'print(1)'"
    python tools/board_jupyter.py --push local.bin /home/xilinx/any/path.bin   # 写任意路径
    python tools/board_jupyter.py --put  local.bin notebooks_relative.bin      # 只能写 Jupyter 根目录
    echo "print('hi')" | python tools/board_jupyter.py

注意：这是控制通道，不是产品代码；板端测试脚本仍应放 tools/ 或 board/。
"""
import base64
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import websocket

HOST = "192.168.2.99:9090"
BASE = "http://" + HOST
PASSWORD = "xilinx"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def _call(method, path, body=None, form=None, timeout=900):
    data, headers = None, {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if method in ("POST", "PUT", "DELETE"):
        for c in cj:
            if c.name == "_xsrf":
                headers["X-XSRFToken"] = c.value
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _login():
    _, page = _call("GET", "/login")
    xsrf = ""
    for c in cj:
        if c.name == "_xsrf":
            xsrf = c.value
    m = re.search(r'name="_xsrf"\s+value="([^"]+)"', page)
    if m:
        xsrf = m.group(1)
    code, _ = _call("POST", "/login?next=%2F", form={"password": PASSWORD, "_xsrf": xsrf})
    if code != 200:
        raise SystemExit("login failed: %s" % code)


def run(code, timeout=900.0):
    _login()
    _, raw = _call("GET", "/api/kernels")
    alive = [k for k in json.loads(raw) if k.get("execution_state") in ("idle", "busy", "starting")]
    if alive:
        kid = alive[0]["id"]
    else:
        _, raw = _call("POST", "/api/kernels", {"name": "python3"})
        time.sleep(1.0)
        kid = json.loads(raw)["id"]
    session = uuid.uuid4().hex
    ws = websocket.create_connection(
        "ws://%s/api/kernels/%s/channels?session_id=%s" % (HOST, kid, session),
        timeout=timeout,
        cookie="; ".join("%s=%s" % (c.name, c.value) for c in cj),
        origin=BASE,
    )
    msg_id = uuid.uuid4().hex
    ws.send(json.dumps({
        "header": {"msg_id": msg_id, "username": "xilinx", "session": session,
                   "msg_type": "execute_request", "version": "5.2"},
        "parent_header": {}, "metadata": {}, "channel": "shell",
        "content": {"code": code, "silent": False, "store_history": False,
                    "user_expressions": {}, "allow_stdin": False, "stop_on_error": True},
    }))
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = json.loads(ws.recv())
        if msg.get("parent_header", {}).get("msg_id") != msg_id:
            continue
        mt = msg.get("msg_type")
        if mt == "stream":
            sys.stdout.buffer.write(msg["content"].get("text", "").encode("utf-8", "replace"))
            sys.stdout.flush()
        elif mt == "error":
            sys.stderr.write("\n".join(msg["content"].get("traceback", [])))
        elif mt == "execute_reply":
            ws.close()
            return 0 if msg["content"].get("status") == "ok" else 1
    ws.close()
    raise SystemExit("TIMEOUT")


def put(local, remote):
    _login()
    raw = open(local, "rb").read()
    code, body = _call("PUT", "/api/contents/" + urllib.parse.quote(remote),
                       {"type": "file", "format": "base64",
                        "content": base64.b64encode(raw).decode("ascii")})
    print("PUT %s -> %s (%d bytes) %s" % (remote, code, len(raw), "" if code < 300 else body[:160]))
    return 0 if code in (200, 201) else 1


def push(local, remote, chunk=262144):
    """把本地文件写到板子上任意路径，并回读 sha256 校验。

    为什么不用 `put()`：Jupyter 的 contents API 只能写服务器根目录
    （=/home/xilinx/jupyter_notebooks）下的路径，`put('/home/xilinx/zynq-yolo/...')`
    会 404/500。这里改走 kernel：把文件切片 base64 塞进内核代码里写盘，
    顺带打印两端 sha256，回环可核。
    """
    import hashlib
    raw = open(local, "rb").read()
    b64 = base64.b64encode(raw).decode("ascii")
    sha_local = hashlib.sha256(raw).hexdigest()
    first = True
    total = 0
    for i in range(0, len(b64), chunk):
        piece = b64[i:i + chunk]
        mode = "wb" if first else "ab"
        code = ("import base64, os\n"
                "p = %r\n"
                "os.makedirs(os.path.dirname(p) or '.', exist_ok=True)\n"
                "open(p, %r).write(base64.b64decode(%r))\n"
                % (remote, mode, piece))
        run(code)
        first = False
        total += len(piece)
    code = ("import base64, hashlib, os\n"
            "p = %r\n"
            "d = open(p, 'rb').read()\n"
            "print('BOARD_PUSH %%s bytes=%%d sha256=%%s' %% (p, len(d), hashlib.sha256(d).hexdigest()))\n"
            % remote)
    run(code)
    print("HOST_PUSH %s bytes=%d sha256=%s chunks=%d"
          % (local, len(raw), sha_local, (total + chunk - 1) // chunk))
    return 0


def get(remote, local):
    _login()
    code, body = _call("GET", "/api/contents/" + urllib.parse.quote(remote) + "?content=1&format=base64")
    if code != 200:
        print("GET %s -> %s %s" % (remote, code, body[:160]))
        return 1
    d = json.loads(body)
    raw = base64.b64decode(d["content"])
    with open(local, "wb") as f:
        f.write(raw)
    print("GET %s -> %s (%d bytes)" % (remote, local, len(raw)))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--put":
        raise SystemExit(put(sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--push":
        raise SystemExit(push(sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--get":
        raise SystemExit(get(sys.argv[2], sys.argv[3]))
    code = sys.stdin.read() if len(sys.argv) < 2 else " ".join(sys.argv[1:])
    raise SystemExit(run(code))