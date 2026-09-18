#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
swobs.py — 运动世界校园 · OBS 轨迹对象上传

复刻真实 App 的第二阶段上传（第一阶段是 save/record 提交汇总）：
  ① POST /api/obs/temporary/url  换签名 URL
       body: {"bucketName":"iydsj-hbase-hot","objectKey":<key>,
              "method":"Put","contentType":"application/json"}
       → data.signedUrl
  ② PUT <signedUrl>  body = 10 键 OBS 对象（每值 gzip+base64）

两个 objectKey：
    run_data/{YYYYMMDDHH}/{uuid}.json
    run_data/{rrid//1000000}/{rrid}.json

10 键 OBS 对象：
    rrid, uuid, uid, run_data, fixed_point_json, segment_json,
    speed_json, step_freq_json, laps_json, runFaceCheck

为什么必须做：只提交汇总数据（第一步）而不上传轨迹（第二步），
服务端拿不到 GPS 轨迹 → 记录会显示默认位置（如北京）而非真实跑点。
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import sys
import time
from base64 import b64encode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swclient as sw

OBS_SIGN_PATH = "/api/obs/temporary/url"
OBS_BUCKET = "iydsj-hbase-hot"

X_PI = math.pi * 3000.0 / 180.0


# ══════════════════════════════════════════════════════════════════
# 坐标转换：BD-09 → GCJ-02
# ══════════════════════════════════════════════════════════════════
def bd09_to_gcj02(bd_lat: float, bd_lng: float):
    """百度 BD-09 → 高德 GCJ-02（与 wire.rs 完全一致）"""
    x = bd_lng - 0.0065
    y = bd_lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    return z * math.sin(theta), z * math.cos(theta)


def wgs84_to_gcj02(lat: float, lng: float):
    """WGS-84 → GCJ-02（中国国测局偏移）。生成器输出的是标准坐标，
    提交时需先转成 GCJ-02 才能在 App 地图上落到正确位置。"""
    a = 6378245.0
    ee = 0.00669342162296594323

    def _transform_lat(x, y):
        ret = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
               + 0.2 * math.sqrt(abs(x)))
        ret += (20.0 * math.sin(6.0 * x * math.pi)
                + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(y * math.pi)
                + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(y / 12.0 * math.pi)
                + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
        return ret

    def _transform_lng(x, y):
        ret = (300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
               + 0.1 * math.sqrt(abs(x)))
        ret += (20.0 * math.sin(6.0 * x * math.pi)
                + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(x * math.pi)
                + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(x / 12.0 * math.pi)
                + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
        return ret

    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
    return lat + dlat, lng + dlng


# ══════════════════════════════════════════════════════════════════
# gzip + base64
# ══════════════════════════════════════════════════════════════════
def gz(data: bytes) -> str:
    """gzip + base64（Rust flate2 Compression::default() == level 6）"""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as f:
        f.write(data)
    return b64encode(buf.getvalue()).decode("ascii")


def gz_json(v) -> str:
    return gz(json.dumps(v, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def gz_str(s: str) -> str:
    return gz(s.encode("utf-8"))


def round_to(v: float, nd: int) -> float:
    f = 10.0 ** nd
    return math.floor(abs(v) * f + 0.5) / f * (1 if v >= 0 else -1)


# ══════════════════════════════════════════════════════════════════
# 27 键协议点集（gen 点 → OBS 点）
# ══════════════════════════════════════════════════════════════════
def conv_point(p: dict, start_ms: int) -> dict:
    """生成器点 → OBS 协议点（27 键）。

    生成器给的是 WGS-84 坐标，提交需 GCJ-02。
    若坐标为 (0,0) 视为无效点，gLat/gLng 置 -1。
    """
    lat, lng = float(p.get("lat", 0) or 0), float(p.get("lon", 0) or 0)
    if lat == 0.0 and lng == 0.0:
        glat, glng = -1.0, -1.0
    else:
        glat, glng = wgs84_to_gcj02(lat, lng)

    t_rel = float(p.get("t_rel", 0) or 0)
    dist = float(p.get("dist", 0) or 0)
    steps = int(p.get("steps", 0) or 0)
    cadence = float(p.get("_cadence", p.get("cadence", 0)) or 0)
    speed = float(p.get("speed", 0) or 0)
    ele = float(p.get("ele", 0) or 0)

    return {
        "avgSpeed": round_to(speed, 4),
        "bdA": round_to(ele, 2),
        "bdD": 0.0,
        "bdG": 0,
        "bdS": 0.0,
        "coorType": "gcj02",
        "count": 1,
        "dtr": 0.0,
        "flag": start_ms,
        "gLat": round_to(glat, 7),
        "gLng": round_to(glng, 7),
        "gainTime": 0,
        "id": int(p.get("i", 0)),
        "lat": -1.0,
        "lng": -1.0,
        "locType": 1,
        "locationId": "",
        "queueNum": 0,
        "radius": 0.0,
        "speed": round_to(speed, 4),
        "state": 0,
        "stepDistance": 0.0,
        "totalDis": round_to(dist, 4),
        "totalTime": int(round(t_rel)),
        "type": 1,
        "validDis": round_to(dist, 4),
        "validTime": int(round(t_rel)),
    }


# ══════════════════════════════════════════════════════════════════
# 10 秒窗 / 圈 / 五点
# ══════════════════════════════════════════════════════════════════
def build_windows(points: list, start_ms: int, total_time: int, rrid: int):
    """10 秒窗 speed / step_freq。

    ★ id 规则（新版本）：id = (rrid % 100000) * 1000 + 窗口右边界秒数
      注意与旧版「全局序号 60000+n」不同 —— 旧版提交不报错但详情页轨迹异常。
    """
    sp, stf = [], []
    w = 10
    while w <= total_time:
        lo, hi = w - 10, min(w, total_time)
        d_lo = s_lo = 0.0
        d_hi = s_hi = 0.0
        for p in points:
            tt = float(p.get("t_rel", 0))
            if tt <= lo:
                d_lo, s_lo = float(p.get("dist", 0)), float(p.get("steps", 0))
            if tt <= hi:
                d_hi, s_hi = float(p.get("dist", 0)), float(p.get("steps", 0))
        dist = round_to(max(d_hi - d_lo, 0.0), 4)
        steps_n = int(max(s_hi - s_lo, 0))
        wid = (rrid % 100000) * 1000 + hi
        sp.append({"beginTime": start_ms + lo * 1000, "distance": dist,
                   "endTime": start_ms + hi * 1000, "flag": start_ms,
                   "id": wid, "queueNum": 0, "state": 0})
        stf.append({"avgDiff": 0.0, "beginTime": start_ms + lo * 1000,
                    "endTime": start_ms + hi * 1000, "flag": start_ms,
                    "id": wid, "maxDiff": 0.0, "minDiff": 1000.0,
                    "queueNum": 0, "state": 0, "stepsNum": steps_n})
        w += 10
    return sp, stf


def build_laps(points: list, start_ms: int) -> list:
    """每 1000m 一圈，末圈 isFullLap=false；avgStride 单位厘米"""
    laps = []
    prev_d = prev_t = prev_steps = 0
    gain = 0.0
    alt0 = float(points[0].get("ele", 0) or 0) if points else 0.0
    for i, p in enumerate(points):
        if i > 0:
            dd = float(p.get("ele", 0) or 0) - float(points[i - 1].get("ele", 0) or 0)
            if dd > 0:
                gain += dd
        d_now = float(p.get("dist", 0) or 0)
        t_now = int(round(float(p.get("t_rel", 0) or 0)))
        last = i == len(points) - 1
        if d_now - prev_d >= 1000.0 or last:
            lap_d = d_now - prev_d
            lap_t = max(1, t_now - prev_t)
            lap_steps = int(p.get("steps", 0) or 0) - prev_steps
            laps.append({
                "avgCadence": round_to(lap_steps / (lap_t / 60.0), 2),
                "avgPace": round_to((lap_t / 60.0) / max(lap_d / 1000.0, 0.001), 2),
                "avgStride": round_to(lap_d / max(1, lap_steps) * 100.0, 2),
                "cumulativeDuration": t_now,
                "distance": round_to(lap_d, 4),
                "duration": lap_t,
                "elevationGain": round_to(gain, 2),
                "endAltAbs": round_to(float(p.get("ele", 0) or 0), 2),
                "endAltRel": round_to(float(p.get("ele", 0) or 0) - alt0, 2),
                "flag": start_ms,
                "id": len(laps) + 1,
                "isFullLap": lap_d >= 1000.0,
                "lapIndex": len(laps) + 1,
                "step": lap_steps,
            })
            prev_d, prev_t, prev_steps = d_now, t_now, int(p.get("steps", 0) or 0)
            gain = 0.0
    return laps


def five_point_payload(points: list, start_ms: int) -> list:
    """五点实体（跑完态 isPass=true）。

    ★★ 只接受【服务端下发的打卡点】，绝不接受轨迹点 ★★
      · 自由跑：无围栏、无打卡点 → 传 []，fivePointJson 序列化为 "[]"
      · 计分跑：传学校下发的点位（通常 3~5 个，isFixed=1 为必经点）

    历史 bug（已修）：本函数原先对【轨迹点】逐点生成 isPass=true 的"假打卡点"，
    导致 2km 自由跑（144 个轨迹点）往 fixed_point_json 里塞 144 个点位，
    5km 就是上千个 —— 与真实 App 的「自由跑无点位」完全不符。

    字段实现委托给 swsubmit.five_point_payload，保证提交 body 与 OBS 对象
    里的 fivePointJson 结构完全一致（单一实现，避免两份漂移）。
    """
    if not points:
        return []
    import swsubmit
    return swsubmit.five_point_payload(points, start_ms)


# ══════════════════════════════════════════════════════════════════
# 10 键 OBS 对象 & key 命名
# ══════════════════════════════════════════════════════════════════
def obs_keys(start_ms: int, rrid: int, uuid: str) -> list:
    t0 = time.strftime("%Y%m%d%H", time.localtime(start_ms / 1000.0))
    return ["run_data/{}/{}.json".format(t0, uuid),
            "run_data/{}/{}.json".format(rrid // 1000000, rrid)]


def build_obs_object(points: list, *, rrid: int, uuid: str, uid: int,
                     start_ms: int, total_time: int, with_steps: bool = True,
                     fixed_points: list = None) -> dict:
    """组装 10 键 OBS 对象（值均 gzip+base64）

    with_steps 参数保留以兼容旧调用，但自由跑与计分跑【都要】完整
    步频/步幅数据（详情页图表数据源），不再清零。

    fixed_points：【服务端下发的打卡点】，只用于 fixed_point_json 里的
      fivePointJson。
        · 自由跑 → 传 [] 或 None → fivePointJson = "[]"（无点位）
        · 计分跑 → 传学校下发的点位
      ★ 绝不传轨迹点：轨迹点属于 run_data.allLocJson，两者是不同的东西。
    """
    pts = [conv_point(p, start_ms) for p in points]
    run_wrap = {"allLocJson": json.dumps(pts, separators=(",", ":"),
                                        ensure_ascii=False),
                "useZip": False}
    sp, stf = build_windows(points, start_ms, total_time, rrid)
    laps = build_laps(points, start_ms)
    five = five_point_payload(list(fixed_points or []), start_ms)
    fx = {"fivePointJson": json.dumps(five, separators=(",", ":"),
                                      ensure_ascii=False),
          "freedomShowFence": False,
          "geoFencesJson": "[]",
          "runAreaId": -1,
          "useZip": False}
    return {
        "rrid": gz_str(str(rrid)),
        "uuid": gz_str(uuid),
        "uid": gz_str(str(uid)),
        "run_data": gz_json(run_wrap),
        "fixed_point_json": gz_json(fx),
        "segment_json": gz_str(""),
        "speed_json": gz_json(sp),
        "step_freq_json": gz_json(stf),
        "laps_json": gz_json(laps),
        "runFaceCheck": gz_str(""),
    }


# ══════════════════════════════════════════════════════════════════
# OBS 上传（换签名 URL → PUT）
# ══════════════════════════════════════════════════════════════════
def sign_url(call_fn, key: str, method: str = "Put") -> str:
    body = json.dumps({"bucketName": OBS_BUCKET, "objectKey": key,
                       "method": method, "contentType": "application/json"},
                      separators=(",", ":"))
    _, biz, err, _ = call_fn("POST", OBS_SIGN_PATH, body)
    if biz is None:
        raise RuntimeError("OBS 签名失败: %s" % err)
    d = biz.get("data")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            pass
    signed = None
    if isinstance(d, dict):
        signed = d.get("signedUrl") or d.get("signedURL") or d.get("url")
    if not signed:
        signed = biz.get("signedUrl")
    if not signed:
        raise RuntimeError("OBS 签名响应缺 signedUrl: %s"
                           % json.dumps(biz, ensure_ascii=False)[:300])
    return signed


def put_object(signed_url: str, payload: bytes) -> int:
    import urllib.request
    import urllib.error
    req = urllib.request.Request(signed_url, data=payload, method="PUT",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        raise RuntimeError("OBS PUT HTTP %d: %s"
                           % (e.code, e.read().decode("utf-8", "replace")[:200]))


def upload_track(call_fn, points: list, *, rrid: int, uuid: str, uid: int,
                 start_ms: int, total_time: int, with_steps: bool = True,
                 fixed_points: list = None, verbose: bool = True):
    """完整 OBS 上传：组装 → 换签名 → 双 key PUT。返回成功数。

    fixed_points：服务端下发的打卡点（自由跑传 [] / 不传 → fivePointJson="[]"）。
    """
    obj = build_obs_object(points, rrid=rrid, uuid=uuid, uid=uid,
                           start_ms=start_ms, total_time=total_time,
                           with_steps=with_steps,
                           fixed_points=fixed_points)
    payload = json.dumps(obj, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    keys = obs_keys(start_ms, rrid, uuid)
    if verbose:
        print("  OBS 对象 %d 字节  key 数=%d" % (len(payload), len(keys)))
    ok = 0
    for k in keys:
        try:
            url = sign_url(call_fn, k, "Put")
            st = put_object(url, payload)
            if verbose:
                print("  PUT %s -> %d" % (k, st))
            ok += 1
        except Exception as e:
            if verbose:
                print("  [warn] PUT %s 失败: %s" % (k, e))
    return ok, keys


def read_back(call_fn, key: str, timeout: int = 30):
    """用 GET 签名 URL 回读对象原文（bytes）。失败返回 None。

    注意：部分 OBS 桶不允许 GET 临时签名，此时会抛错；调用方应容错。
    """
    import urllib.request
    import urllib.error
    url = sign_url(call_fn, key, "Get")
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("OBS GET HTTP %d: %s"
                           % (e.code, e.read().decode("utf-8", "replace")[:200]))


def decode_point_count(obj: dict) -> int:
    """从回读的 OBS 对象里解出轨迹点数量（run_data 的 allLocJson）"""
    import gzip
    import base64
    try:
        rd = json.loads(gzip.decompress(
            base64.b64decode(obj.get("run_data") or "")).decode("utf-8"))
        locs = rd.get("allLocJson") or "[]"
        if isinstance(locs, str):
            locs = json.loads(locs)
        return len(locs)
    except Exception:
        return 0


def selftest() -> bool:
    ok = True
    print("=" * 62)
    print("swobs 自检")
    print("=" * 62)

    # 1) 坐标转换
    a, b = wgs84_to_gcj02(22.981367, 116.332141)
    print("  WGS84(22.981367,116.332141) -> GCJ02(%.7f,%.7f)" % (a, b))
    ok &= abs(a - 22.981367) > 1e-5  # 转换后必然有偏移
    ok &= abs(a - 22.981367) < 0.01
    ok &= abs(b - 116.332141) < 0.01
    print("  %s GCJ-02 偏移量合理" % ("OK " if ok else "FAIL"))

    # 2) gzip round-trip
    s = "运动世界校园"
    import base64
    import gzip as _gz
    back = _gz.decompress(base64.b64decode(gz_str(s))).decode("utf-8")
    print("  %s gzip+base64 往返 == 原文" % ("OK " if back == s else "FAIL"))
    ok &= back == s

    # 3) obs_keys
    ks = obs_keys(1789534834000, 1322680573, "TEST-UUID-6BB5-4895-BEB8-E0B7C110EC26")
    exp_rrid = 1322680573 // 1000000
    print("  keys = %s" % ks)
    ok &= ("run_data/%d/1322680573.json" % exp_rrid) in ks
    print("  %s rrid key 命名正确" % ("OK " if ok else "FAIL"))

    # 4) 10 键完整性
    pts = [{"i": 0, "t_rel": 0.0, "lat": 22.981367, "lon": 116.332141,
            "ele": 100.0, "speed": 2.0, "dist": 0.0, "steps": 0, "cadence": 160},
           {"i": 1, "t_rel": 10.0, "lat": 22.981400, "lon": 116.332180,
            "ele": 101.0, "speed": 3.0, "dist": 25.0, "steps": 27, "cadence": 162}]
    obj = build_obs_object(pts, rrid=1322680573, uuid="UUID-T",
                           uid=12345678, start_ms=1789534834000, total_time=10)
    need = ["rrid", "uuid", "uid", "run_data", "fixed_point_json",
            "segment_json", "speed_json", "step_freq_json", "laps_json",
            "runFaceCheck"]
    miss = [k for k in need if k not in obj]
    print("  %s 10 键齐全 %s" % ("OK " if not miss else "FAIL", miss or ""))
    ok &= not miss
    print("  %s 全部值为字符串" % ("OK " if all(isinstance(v, str) for v in obj.values()) else "FAIL"))
    ok &= all(isinstance(v, str) for v in obj.values())

    # 5) run_data 可解回，且含 allLocJson 27 键
    import base64
    rd = json.loads(_gz.decompress(base64.b64decode(obj["run_data"])).decode("utf-8"))
    locs = json.loads(rd["allLocJson"])
    n27 = len(locs[0]) if locs else 0
    print("  run_data 轨迹点数=%d 单点键数=%d" % (len(locs), n27))
    ok &= len(locs) == 2 and n27 == 27
    print("  %s 27 键协议点集" % ("OK " if n27 == 27 else "FAIL"))
    print("  %s coorType=gcj02" % ("OK " if locs[0]["coorType"] == "gcj02" else "FAIL"))
    ok &= locs[0]["coorType"] == "gcj02"

    # 6) ★★ 五点来源：轨迹点【不得】出现在 fixed_point_json 里
    fx = json.loads(_gz.decompress(
        base64.b64decode(obj["fixed_point_json"])).decode("utf-8"))
    five_free = json.loads(fx["fivePointJson"])
    print("  [自由跑] fixed_point_json 点位数=%d (期望 0)" % len(five_free))
    print("  %s 自由跑 fivePointJson == \"[]\"（无点位）"
          % ("OK " if len(five_free) == 0 else "FAIL"))
    ok &= len(five_free) == 0

    cps = [{"pointName": "一号点", "lat": 22.98, "lon": 116.33,
            "glat": 22.981, "glon": 116.335, "radius": 15.0, "isFixed": 1},
           {"pointName": "二号点", "lat": 22.99, "lon": 116.34,
            "glat": 22.991, "glon": 116.345, "radius": 15.0, "isFixed": 0}]
    obj2 = build_obs_object(pts, rrid=1322680573, uuid="UUID-T", uid=12345678,
                            start_ms=1789534834000, total_time=10,
                            fixed_points=cps)
    fx2 = json.loads(_gz.decompress(
        base64.b64decode(obj2["fixed_point_json"])).decode("utf-8"))
    five2 = json.loads(fx2["fivePointJson"])
    good2 = (len(five2) == 2 and five2[0]["pointName"] == "一号点"
             and five2[0]["isFixed"] == 1 and "lon" in five2[0]
             and "lng" not in five2[0])
    print("  [计分跑] fixed_point_json 点位数=%d (期望 2)" % len(five2))
    print("  %s 计分跑五点 = 真实打卡点（pointName/isFixed/lon 正确）"
          % ("OK " if good2 else "FAIL"))
    ok &= good2

    # 7) id 规则
    print("  speed_json[0].id 规则: (rrid%%100000)*1000+hi")
    spj = json.loads(_gz.decompress(base64.b64decode(obj["speed_json"])).decode("utf-8"))
    exp_id = (1322680573 % 100000) * 1000 + 10
    print("  %s id=%d (期望 %d)" % ("OK " if spj[0]["id"] == exp_id else "FAIL",
                                    spj[0]["id"], exp_id))
    ok &= spj[0]["id"] == exp_id

    print("=" * 62)
    print("汇总: %s" % ("全部通过" if ok else "存在失败"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if selftest() else 1)
