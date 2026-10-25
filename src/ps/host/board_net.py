#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_net.py -- board-side plan loader and full-network PL backend.

This module is the board-side half of the streaming test:

    data/layers/board_net_plan.json
        -> load_plan()           JSON -> pynq_net.Graph + raw layer entries
        -> make_layer_plan()     entry + .bin files -> pynq_net.LayerPlan
        -> BoardConvDriver       one reusable PYNQ buffer set, one conv per call
        -> BoardBackend          pynq_net.Backend, PL for supported layers
        -> run_frame()           RGB frame -> detections + timings

It runs on the board (PYNQ 2.7, Python 3.8, numpy 1.17.4).  Importing this
module on a PC is safe as long as numpy is present; pynq is imported lazily by
BoardConvDriver, so no overlay work happens at import time.

Public API:
    load_plan(path)                     -> BoardPlan
    make_layer_plan(entry, layers_dir)  -> pynq_net.LayerPlan
    BoardConvDriver(overlay, plan, layers_dir)
    BoardBackend(driver)
    run_frame(rgb_hwc, graph, plans, backend, conf=0.25, iou=0.45)
    class_name(class_id)                -> str

Notes:
  * All paths in plan["layers"][name]["files"] are relative to layers_dir.
  * ndarray values in initializers/attrs are little-endian raw bytes in base64.
  * stdout is not used by this module; the CLI owns the ASCII tokens.
"""

import sys

# Must be set before importing pynq_net / any local module: the board must not
# create __pycache__ directories (tools/check_repo.py check d).
sys.dont_write_bytecode = True

import base64
import json
import os
import time
from pathlib import Path

import numpy as np
import numpy.lib.stride_tricks as _stride_tricks

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")

# The board receives tools/pynq_net.py.  It only imports onnx inside
# load_graph(), so importing the module itself is safe on the board.
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import pynq_net  # noqa: E402  (path setup must happen first)

PLAN_SCHEMA = "zynq-yolo/board-stream-plan/v1"
DEFAULT_CONF = 0.25
DEFAULT_IOU = 0.45
LUT_SIZE = 256

# COCO class ids used by the streaming test; everything else gets class_<id>.
CLASS_NAMES = {
    0: "person",
    32: "sports_ball",
}

NUMPY_COMPAT_INSTALLED = False


# ---------------------------------------------------------------------------
# numpy 1.17 compatibility
# ---------------------------------------------------------------------------
def _install_numpy_compat():
    """Provide sliding_window_view on numpy < 1.20.

    tools/pynq_net.py uses np.lib.stride_tricks.sliding_window_view in
    _op_maxpool() and _im2col().  That function was added in numpy 1.20, while
    the board image ships numpy 1.17.4.  The shim below is a pure view, has the
    same output layout (window axes appended at the end), and only reads data.
    It is installed only when the native function is missing.
    """
    global NUMPY_COMPAT_INSTALLED
    if hasattr(_stride_tricks, "sliding_window_view"):
        return False

    def sliding_window_view(x, window_shape, axis=None, subok=False,
                            writeable=False):
        x = np.asarray(x)
        if np.isscalar(window_shape):
            window_shape = (int(window_shape),)
        else:
            window_shape = tuple(int(v) for v in window_shape)
        if axis is None:
            axis = tuple(range(x.ndim - len(window_shape), x.ndim))
        else:
            axis = tuple(int(a) % x.ndim for a in axis)
        if len(window_shape) != len(axis):
            raise ValueError("window_shape and axis must have the same length")
        out_shape = list(x.shape)
        out_strides = list(x.strides)
        for win, ax in zip(window_shape, axis):
            if win < 1 or win > x.shape[ax]:
                raise ValueError(
                    "window %d does not fit axis %d of shape %r"
                    % (win, ax, x.shape))
            out_shape[ax] = x.shape[ax] - win + 1
        out_shape.extend(window_shape)
        out_strides.extend(x.strides[ax] for ax in axis)
        return _stride_tricks.as_strided(
            x, shape=tuple(out_shape), strides=tuple(out_strides),
            writeable=writeable)

    _stride_tricks.sliding_window_view = sliding_window_view
    NUMPY_COMPAT_INSTALLED = True
    return True


_install_numpy_compat()


# ---------------------------------------------------------------------------
# Plan decoding
# ---------------------------------------------------------------------------
def _decode_array_obj(obj, where):
    """Decode {"dtype", "shape", "data_b64"} into a fresh ndarray."""
    try:
        dtype = np.dtype(obj["dtype"])
        shape = tuple(int(v) for v in obj.get("shape", []))
        data_b64 = obj["data_b64"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("malformed ndarray at %s: %r" % (where, exc))
    raw = base64.b64decode(data_b64)
    count = 1
    for dim in shape:
        count *= dim
    expected = count * dtype.itemsize
    if len(raw) != expected:
        raise ValueError(
            "ndarray at %s: %d bytes decoded, expected %d (shape %r dtype %s)"
            % (where, len(raw), expected, shape, dtype.name))
    arr = np.frombuffer(raw, dtype=dtype).reshape(shape)
    return np.array(arr, copy=True)


def _decode_value(value, where):
    """Recursively decode __ndarray__ markers inside attrs/lists/dicts."""
    if isinstance(value, dict):
        if value.get("__ndarray__") is True:
            return _decode_array_obj(value, where)
        return dict((key, _decode_value(val, where + "/" + str(key)))
                    for key, val in value.items())
    if isinstance(value, list):
        return [_decode_value(val, where) for val in value]
    return value


def _decode_initializer(value, name):
    """Initializers use either the __ndarray__ form or a direct encoding."""
    if isinstance(value, dict) and value.get("__ndarray__") is True:
        return _decode_array_obj(value, "initializers/" + str(name))
    if isinstance(value, dict) and "data_b64" in value:
        return _decode_array_obj(value, "initializers/" + str(name))
    raise ValueError("initializer %r is not an encoded ndarray" % (name,))


class BoardPlan(object):
    """Parsed board-stream plan: graph, raw JSON, and per-conv layer entries."""

    def __init__(self, path, raw, graph, input_quant, output_name):
        self.path = path
        self.raw = raw
        self.graph = graph
        self.input_quant = input_quant
        self.output_name = output_name
        self.layers = raw.get("layers") or {}

    def conv_entries(self):
        """Return layer entries in graph node order (only Conv nodes)."""
        entries = []
        for node in self.graph.nodes:
            if node.op_type != "Conv":
                continue
            try:
                entry = self.layers[node.name]
            except KeyError:
                raise ValueError(
                    "plan has no layer entry for Conv node %r" % node.name)
            conv_name = str(entry.get("conv_name", node.name))
            if conv_name != node.name:
                raise ValueError(
                    "layer entry for Conv node %r declares conv_name %r; "
                    "run_network keys plans by node name" % (
                        node.name, conv_name))
            entries.append(entry)
        return entries


def load_plan(path):
    """Load a board-stream plan and rebuild a pynq_net.Graph.

    The graph is only used by pynq_net.run_network(), which needs nodes,
    initializers and input_name.  Shapes are not used there, so Graph.shapes
    is left empty; every Conv layer carries its geometry in LayerPlan instead.
    """
    path = os.path.abspath(path)
    with open(path, "r", encoding="utf-8", newline="\n") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError("plan root is not a JSON object: %s" % path)
    if raw.get("schema") != PLAN_SCHEMA:
        raise ValueError(
            "unsupported plan schema %r (expected %r)"
            % (raw.get("schema"), PLAN_SCHEMA))

    try:
        input_name = str(raw["input_name"])
        input_shape = [int(v) for v in raw["input_shape"]]
        output_name = str(raw["output_name"])
    except KeyError as exc:
        raise ValueError("plan missing required key: %s" % exc)
    except (TypeError, ValueError) as exc:
        raise ValueError("plan has a malformed input_shape: %r" % exc)
    if len(input_shape) != 4 or input_shape[0] != 1 or input_shape[1] != 3:
        raise ValueError(
            "plan input_shape must be [1,3,H,W], got %r" % (input_shape,))

    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("plan nodes must be a list")
    nodes = []
    for idx, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            raise ValueError("plan node %d is not an object" % idx)
        attrs = _decode_value(raw_node.get("attrs") or {},
                              "nodes[%d].attrs" % idx)
        nodes.append(pynq_net.Node(
            idx=idx,
            op_type=str(raw_node.get("op_type", "")),
            name=str(raw_node.get("name", "")),
            inputs=[str(v) for v in (raw_node.get("inputs") or [])],
            outputs=[str(v) for v in (raw_node.get("outputs") or [])],
            attrs=attrs,
        ))

    raw_init = raw.get("initializers")
    if not isinstance(raw_init, dict):
        raise ValueError("plan initializers must be an object")
    initializers = {}
    for name, value in raw_init.items():
        initializers[str(name)] = _decode_initializer(value, name)

    # pynq_net.run_network() expects Constant values under initializers.  The
    # pack generator normally does this already; merging is idempotent.
    for node in nodes:
        if node.op_type != "Constant" or not node.outputs:
            continue
        out_name = node.outputs[0]
        if out_name in initializers:
            continue
        value = node.attrs.get("value")
        if isinstance(value, np.ndarray):
            initializers[out_name] = value
        else:
            raise ValueError(
                "Constant node %r has no decoded value and is not in "
                "initializers" % node.name)

    try:
        layers = raw["layers"]
    except KeyError:
        raise ValueError("plan missing required key: 'layers'")
    if not isinstance(layers, dict):
        raise ValueError("plan layers must be an object keyed by conv_name")

    graph = pynq_net.Graph(
        path=Path(path),
        nodes=nodes,
        initializers=initializers,
        input_name=input_name,
        input_shape=input_shape,
        output_names=[output_name],
        opset=0,
    )
    input_quant = raw.get("input_quant") or {}
    if not isinstance(input_quant, dict):
        raise ValueError("plan input_quant must be an object")
    return BoardPlan(path, raw, graph, input_quant, output_name)


# ---------------------------------------------------------------------------
# LayerPlan construction
# ---------------------------------------------------------------------------
def _read_bin(path, dtype, count, label):
    """Read exactly count elements of dtype from a raw little-endian file."""
    if not os.path.isfile(path):
        raise ValueError("missing %s file: %s" % (label, path))
    expected_bytes = int(count) * np.dtype(dtype).itemsize
    actual_bytes = os.path.getsize(path)
    if actual_bytes != expected_bytes:
        raise ValueError(
            "%s file size mismatch: %s has %d bytes, expected %d "
            "(%d elements of %s)" % (
                label, path, actual_bytes, expected_bytes, count,
                np.dtype(dtype).name))
    arr = np.fromfile(path, dtype=dtype)
    if arr.size != count:
        raise ValueError(
            "%s read %d elements from %s, expected %d"
            % (label, arr.size, path, count))
    return arr


def make_layer_plan(entry, layers_dir):
    """Build a pynq_net.LayerPlan from one plan["layers"][conv_name] entry.

    w.bin is int8 [C_OUT][C_IN][K][K] and is reshaped to
    [C_OUT][C_IN*K*K] for pynq_net's int8_conv fallback.  params.bin is
    int32 [C_OUT][3] = {bias_i32, M, S}.  lut.bin is uint8 [256].
    """
    try:
        conv_name = str(entry["conv_name"])
        in_name = str(entry["in_name"])
        out_name = str(entry["out_name"])
        geom = entry["geom"]
        quant = entry["quant"]
        files = entry["files"]
    except KeyError as exc:
        raise ValueError("layer entry missing required key: %s" % exc)

    c_in = int(geom["C_IN"])
    c_out = int(geom["C_OUT"])
    h_in = int(geom["H_IN"])
    w_in = int(geom["W_IN"])
    h_out = int(geom["H_OUT"])
    w_out = int(geom["W_OUT"])
    k = int(geom["K"])
    stride = int(geom["stride"])
    pad = int(geom["pad"])
    if min(c_in, c_out, h_in, w_in, h_out, w_out, k, stride) <= 0:
        raise ValueError("layer %s has a non-positive geometry field" % conv_name)

    w_count = c_out * c_in * k * k
    w_path = os.path.join(layers_dir, files["w"])
    params_path = os.path.join(layers_dir, files["params"])
    lut_path = os.path.join(layers_dir, files["lut"])

    w_q = _read_bin(w_path, np.int8, w_count, "w(%s)" % conv_name)
    w_q = w_q.reshape(c_out, c_in * k * k)
    params = _read_bin(
        params_path, np.int32, c_out * 3, "params(%s)" % conv_name)
    params = params.reshape(c_out, 3)
    lut = _read_bin(lut_path, np.uint8, LUT_SIZE, "lut(%s)" % conv_name)

    bias_i32 = np.array(params[:, 0], dtype=np.int32, copy=True)
    m = np.array(params[:, 1], dtype=np.int32, copy=True)
    s = np.array(params[:, 2], dtype=np.int32, copy=True)

    macs = entry.get("macs")
    if macs is None:
        macs = c_out * c_in * k * k * h_out * w_out
    macs = int(macs)

    return pynq_net.LayerPlan(
        conv_name=conv_name,
        in_name=in_name,
        out_name=out_name,
        c_in=c_in,
        c_out=c_out,
        k=k,
        stride=stride,
        pad_h_begin=pad,
        pad_h_end=pad,
        pad_w_begin=pad,
        pad_w_end=pad,
        h_in=h_in,
        w_in=w_in,
        h_out=h_out,
        w_out=w_out,
        w_f=None,
        w_q=w_q,
        w_scale=np.ones(c_out, dtype=np.float32),
        bias_f=None,
        bias_i32=bias_i32,
        m=m,
        s=s,
        sx=float(quant["sx"]),
        zx=int(quant["zx"]),
        sp=float(quant["sp"]),
        zp_pre=int(quant["zp_pre"]),
        so=float(quant["so"]),
        zo=int(quant["zo"]),
        act="none",
        lut=lut,
        macs=macs,
    )


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------
class BoardBackend(pynq_net.Backend):
    """pynq_net backend: PL for v1-supported convs, numpy for the rest."""

    name = "board"
    quantized = True

    def __init__(self, driver):
        self.driver = driver
        self._numpy = pynq_net.NumpyBackend()
        self.pl_layers = []
        self.fallback_layers = []

    def conv(self, xq, layer):
        ok, why = layer.vs_contract()
        if not ok:
            self.fallback_layers.append((layer.conv_name, why))
            return self._numpy.conv(xq, layer)
        self.pl_layers.append(layer.conv_name)
        return self.driver.run_conv(layer, xq)


# ---------------------------------------------------------------------------
# PL driver with one reusable buffer set
# ---------------------------------------------------------------------------
def _plan_layers(plan):
    layers = getattr(plan, "layers", None)
    if isinstance(layers, dict):
        return layers
    if isinstance(plan, dict):
        layers = plan.get("layers")
        if isinstance(layers, dict):
            return layers
    raise TypeError("driver needs a BoardPlan or a dict with a 'layers' object")


class BoardBufferAllocError(RuntimeError):
    """PYNQ CMA allocation failed (exact pynq exception is in the message)."""


class BoardConvDriver(object):
    """Run one convolution on conv_engine with a single reusable buffer set.

    The board has only ~1.4 MB of CmaFree while PYNQ/Jupyter is running, so
    this class allocates exactly five PYNQ buffers, sized to the largest
    layer/tensor:

        w       uint8 [max C_OUT*C_IN*K*K]
        params  uint8 [max C_OUT*3*4]
        lut     uint8 [256]
        in_r    uint8 [max C_IN*H_IN*W_IN]
        out_r   uint8 [max C_OUT*H_OUT*W_OUT]

    run_conv() copies the LayerPlan's NumPy w/params/lut bytes into those
    buffers, flushes them, sets overlay._meta and overlay._c2c_buf, and calls
    overlay.run_once(meta).  It never calls overlay.load_layer() and never
    keeps per-layer PYNQ buffers.
    """

    def __init__(self, overlay, plan, layers_dir):
        try:
            from pynq import allocate
        except ImportError as exc:
            raise ImportError(
                "pynq not available: BoardConvDriver must run on the board "
                "(PYNQ 2.7). Original error: %s" % exc)
        self.overlay = overlay
        self.plan = plan
        self.layers_dir = layers_dir
        self._entries = _plan_layers(plan)
        if not self._entries:
            raise ValueError("plan has no layer entries")
        self.layer_count = len(self._entries)

        max_in = 0
        max_out = 0
        max_w_bytes = 0
        max_params_elems = 0
        for name, entry in self._entries.items():
            try:
                geom = entry["geom"]
            except KeyError as exc:
                raise ValueError(
                    "layer entry %r missing required key: %s" % (name, exc))
            c_in = int(geom["C_IN"])
            c_out = int(geom["C_OUT"])
            k = int(geom["K"])
            if min(c_in, c_out, k) <= 0:
                raise ValueError(
                    "layer entry %r has non-positive geometry" % (name,))
            n_in = c_in * int(geom["H_IN"]) * int(geom["W_IN"])
            n_out = c_out * int(geom["H_OUT"]) * int(geom["W_OUT"])
            _kk = k * k
            _rb = 4 if _kk <= 4 else 12
            n_w = c_out * c_in * _rb
            n_params = c_out * 3
            if n_in > max_in:
                max_in = n_in
            if n_out > max_out:
                max_out = n_out
            if n_w > max_w_bytes:
                max_w_bytes = n_w
            if n_params > max_params_elems:
                max_params_elems = n_params
        if max_in <= 0 or max_out <= 0 or max_w_bytes <= 0:
            raise ValueError("plan produced an empty tensor size")

        self.max_in_elems = int(max_in)
        self.max_out_elems = int(max_out)
        self.max_w_bytes = int(max_w_bytes)
        self.max_params_elems = int(max_params_elems)
        self.max_params_bytes = int(max_params_elems) * 4
        self.buffer_bytes = {
            "w": self.max_w_bytes,
            "params": self.max_params_bytes,
            "lut": LUT_SIZE,
            "in": self.max_in_elems,
            "out": self.max_out_elems,
            "total": (self.max_w_bytes + self.max_params_bytes + LUT_SIZE
                      + self.max_in_elems + self.max_out_elems),
        }

        def _alloc(shape, label):
            try:
                return allocate(shape=shape, dtype=np.uint8)
            except Exception as exc:  # noqa: BLE001 - preserve exact error
                raise BoardBufferAllocError(
                    "%s buffer allocate(shape=%r, dtype=uint8) failed: %r"
                    % (label, shape, exc)) from exc

        # All buffers are byte buffers.  int8/int32 payloads are copied as raw
        # little-endian bytes; the PL only sees physical addresses.
        self._w_buf = _alloc((self.max_w_bytes,), "w")
        self._params_buf = _alloc((self.max_params_bytes,), "params")
        self._lut_buf = _alloc((LUT_SIZE,), "lut")
        self._in_buf = _alloc((self.max_in_elems,), "in")
        self._out_buf = _alloc((self.max_out_elems,), "out")

        # Scratch for rebuilding [bias_i32, M, S] as raw int32 bytes without
        # allocating a new NumPy array for every layer call.
        self._params_scratch = np.empty(
            (self.max_params_elems,), dtype=np.int32)

    def run_conv(self, layer, xq):
        """Run one layer; xq is uint8 [C,H,W] or [1,C,H,W]."""
        if layer.conv_name not in self._entries:
            raise KeyError(
                "layer %r is not present in the loaded plan"
                % layer.conv_name)

        x = np.asarray(xq)
        if x.dtype != np.uint8:
            raise TypeError(
                "run_conv expects uint8 activations, got %s" % x.dtype)
        was_4d = (x.ndim == 4)
        if was_4d:
            if x.shape[0] != 1:
                raise ValueError(
                    "run_conv only supports batch=1, got shape %r" % (x.shape,))
            x = x[0]
        if x.ndim != 3:
            raise ValueError(
                "run_conv expects [C,H,W] or [1,C,H,W], got shape %r"
                % (x.shape,))
        c_in, h_in, w_in = (int(v) for v in x.shape)
        if (c_in, h_in, w_in) != (layer.c_in, layer.h_in, layer.w_in):
            raise ValueError(
                "run_conv input shape %r does not match layer %s geometry "
                "%r" % (x.shape, layer.conv_name,
                        (layer.c_in, layer.h_in, layer.w_in)))

        c_out = int(layer.c_out)
        h_out = int(layer.h_out)
        w_out = int(layer.w_out)
        n_in = c_in * h_in * w_in
        out_size = c_out * h_out * w_out
        if n_in > self.max_in_elems:
            raise ValueError(
                "input tensor %d exceeds reusable buffer %d"
                % (n_in, self.max_in_elems))
        if out_size > self.max_out_elems:
            raise ValueError(
                "output tensor %d exceeds reusable buffer %d"
                % (out_size, self.max_out_elems))

        # Activation (uint8) -> reusable in_r buffer.
        flat = np.ascontiguousarray(x, dtype=np.uint8).reshape(-1)
        self._in_buf[:n_in] = flat
        self._in_buf.flush()

        # Weights: LayerPlan.w_q is the same int8 payload as w.bin, already
        # loaded once by make_layer_plan().  Copy raw bytes into the one
        # reusable w buffer (no per-layer PYNQ buffer).
        if layer.w_q is None:
            raise ValueError("layer %s has no w_q" % layer.conv_name)
        w_u8 = np.ascontiguousarray(
            layer.w_q, dtype=np.int8).reshape(-1).view(np.uint8)
        expected_w = c_out * c_in * int(layer.k) * int(layer.k)
        if w_u8.size != expected_w:
            raise ValueError(
                "layer %s w bytes %d != C_OUT*C_IN*K*K %d"
                % (layer.conv_name, w_u8.size, expected_w))
        kk = int(layer.k) * int(layer.k)
        rb = 4 if kk <= 4 else 12
        w_pad = np.zeros((c_out * c_in, rb), dtype=np.uint8)
        w_pad[:, :kk] = w_u8.reshape(c_out * c_in, kk)
        w_pad = w_pad.reshape(-1)
        if w_pad.size > self.max_w_bytes:
            raise ValueError(
                "w tensor %d exceeds reusable buffer %d"
                % (w_pad.size, self.max_w_bytes))
        self._w_buf[:w_pad.size] = w_pad

        # params: [C_OUT][3] = {bias_i32, M, S}, rebuilt as raw int32 bytes.
        n_params = c_out * 3
        if n_params > self.max_params_elems:
            raise ValueError(
                "params tensor %d exceeds reusable buffer %d"
                % (n_params, self.max_params_elems))
        params = self._params_scratch[:n_params].reshape(c_out, 3)
        params[:, 0] = layer.bias_i32
        params[:, 1] = layer.m
        params[:, 2] = layer.s
        params_u8 = params.reshape(-1).view(np.uint8)
        self._params_buf[:params_u8.size] = params_u8

        lut = np.ascontiguousarray(layer.lut, dtype=np.uint8).reshape(-1)
        if lut.size != LUT_SIZE:
            raise ValueError(
                "layer %s lut size %d != %d"
                % (layer.conv_name, lut.size, LUT_SIZE))
        self._lut_buf[:lut.size] = lut

        self._w_buf.flush()
        self._params_buf.flush()
        self._lut_buf.flush()

        meta = {
            "C_IN": c_in,
            "C_OUT": c_out,
            "H_IN": h_in,
            "W_IN": w_in,
            "K": int(layer.k),
            "stride": int(layer.stride),
            "pad": int(layer.pad_h_begin),
            "zp_in": int(layer.zx),
            "zp_pre": int(layer.zp_pre),
            "H_OUT": h_out,
            "W_OUT": w_out,
        }
        self.overlay._meta = meta
        self.overlay._c2c_buf = {
            "in_r": self._in_buf,
            "w": self._w_buf,
            "params": self._params_buf,
            "lut": self._lut_buf,
            "out_r": self._out_buf,
        }

        run = self.overlay.run_once(meta)
        if not run.get("done"):
            raise RuntimeError(
                "conv %s: ap_done timeout after %.6f s (AP_CTRL=%s)"
                % (layer.conv_name, float(run.get("elapsed_s", 0.0)),
                   run.get("ap_ctrl_final", "")))

        self._out_buf.invalidate()
        out = np.array(
            self._out_buf[:out_size], dtype=np.uint8
        ).reshape(c_out, h_out, w_out)
        # run_network stores graph tensors as [1,C,H,W]; NumpyBackend keeps
        # that rank, so the PL path must keep it too.
        return out[None] if was_4d else out


# ---------------------------------------------------------------------------
# Whole-frame entry point
# ---------------------------------------------------------------------------
def class_name(class_id):
    cid = int(class_id)
    return CLASS_NAMES.get(cid, "class_%d" % cid)


def _decode_yolov5(out, conf, iou, imgsz):
    """Classic YOLOv5 head: output0 is [1,N,85], xywh already in pixels."""
    pred = np.asarray(out, dtype=np.float32)[0]
    if pred.ndim != 2 or pred.shape[1] != 85:
        raise ValueError("YOLOv5 output0 shape is not [N,85]: %r" % (pred.shape,))
    xywh = pred[:, :4]
    obj = pred[:, 4]
    cls_prob = pred[:, 5:]
    cls = np.argmax(cls_prob, axis=1)
    scores = obj * cls_prob[np.arange(cls.size), cls]
    keep = scores >= float(conf)
    xywh = xywh[keep]
    scores = scores[keep]
    cls = cls[keep]
    xyxy = np.empty_like(xywh)
    xyxy[:, 0] = xywh[:, 0] - xywh[:, 2] * 0.5
    xyxy[:, 1] = xywh[:, 1] - xywh[:, 3] * 0.5
    xyxy[:, 2] = xywh[:, 0] + xywh[:, 2] * 0.5
    xyxy[:, 3] = xywh[:, 1] + xywh[:, 3] * 0.5
    np.clip(xyxy[:, 0::2], 0, imgsz, out=xyxy[:, 0::2])
    np.clip(xyxy[:, 1::2], 0, imgsz, out=xyxy[:, 1::2])
    keep2 = pynq_net.nms(xyxy, scores, cls.astype(np.float32), float(iou))
    return {
        "boxes": xyxy[keep2],
        "scores": scores[keep2],
        "cls": cls[keep2],
    }


def run_frame(rgb_hwc, graph, plans, backend, conf=DEFAULT_CONF, iou=DEFAULT_IOU):
    """Run RGB uint8 [H,W,3] through the network and decode detections.

    Returns:
        {
          "detections": {"boxes": [N,4] float, "scores": [N] float,
                         "cls": [N] int, "names": [N] str},
          "timings": {"total_ms": float, "conv_ms": float, "other_ms": float},
          "output": np.ndarray float32 [1,84,2100],
        }
    total_ms is measured around the whole frame (preprocess + network +
    decode + NMS); conv_ms/other_ms come from pynq_net.run_network's stats.
    """
    rgb = np.asarray(rgb_hwc)
    if rgb.dtype != np.uint8:
        raise TypeError("run_frame expects uint8 RGB, got %s" % rgb.dtype)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("run_frame expects RGB [H,W,3], got %r" % (rgb.shape,))
    expected = tuple(int(v) for v in graph.input_shape)
    if rgb.shape != (expected[2], expected[3], expected[1]):
        raise ValueError(
            "frame shape %r does not match plan input %r"
            % (rgb.shape, expected))

    t0 = time.perf_counter()
    x = np.transpose(rgb.astype(np.float32) / np.float32(255.0), (2, 0, 1))
    x = np.ascontiguousarray(x[None, ...], dtype=np.float32)

    tensors, stats = pynq_net.run_network(graph, x, backend, plans)
    output_name = graph.output_names[0]
    if output_name not in tensors:
        raise KeyError("run_network did not produce output %r" % output_name)
    out = np.asarray(tensors[output_name])

    if out.ndim == 3 and out.shape[-1] == 85:
        det = _decode_yolov5(out, float(conf), float(iou), int(expected[2]))
        boxes = np.asarray(det["boxes"])
        scores = np.asarray(det["scores"])
        cls = np.asarray(det["cls"])
    else:
        det = pynq_net.decode_detections(out, float(conf))
        keep = pynq_net.nms(det["boxes"], det["scores"], det["cls"], float(iou))
        boxes = np.asarray(det["boxes"])[keep]
        scores = np.asarray(det["scores"])[keep]
        cls = np.asarray(det["cls"])[keep]
    names = [class_name(v) for v in cls]

    total_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "detections": {
            "boxes": boxes,
            "scores": scores,
            "cls": cls,
            "names": names,
        },
        "timings": {
            "total_ms": float(total_ms),
            "conv_ms": float(stats.get("conv_time_s", 0.0)) * 1000.0,
            "other_ms": float(stats.get("other_time_s", 0.0)) * 1000.0,
        },
        "output": out,
    }
