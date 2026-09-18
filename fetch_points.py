#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_points.py — 拉取校园跑「必经打卡点」

接口：POST /api/v560/get/1/distance/1   (sportType=4)
  body: {sportType:4, longitude, latitude, sign, uuid, selectedUnid, runec}
  sign  = MD5( http版URL + SALT )              ← https:// 换 http://
  runec = 信封( f"{uid}{lon:.6f}{lat:.6f}{start_ms整秒}" , observed 序 )

★ 限流：5 分钟内最多 3 次（错误码 10603）
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid as _uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swclient as sw

POINTS_PATH = "/api/v560/get/1/distance/1"


def md5_url_sign(url: str) -> str:
    """URL 签名：https:// → http:// 后 MD5(url + SALT)"""
    http_url = url.replace("https://", "http://", 1)
    return sw.md5_hex((http_url + sw.SALT).encode("utf-8"))


def fetch_points(c, lat: float, lon: float, unid: int, verbose: bool = True):
    """返回 (原始业务 JSON, 点列表)"""
    c._ensure_env()
    url = sw.HOST + POINTS_PATH
    ts = sw.now_ms()
    runec_input = "%d%.6f%.6f%d" % (c.uid, lon, lat, (ts // 1000) * 1000)
    runec_env, _ = c.env.build_envelope(runec_input, "observed")

    body = json.dumps({
        "sportType": 4,
        "longitude": lon,
        "latitude": lat,
        "sign": md5_url_sign(url),
        "uuid": str(_uuid.uuid4()),
        "selectedUnid": str(unid),
        "runec": runec_env,
    }, separators=(",", ":"), ensure_ascii=False)

    _, biz, err, raw = c.call("POST", POINTS_PATH, body, verbose=verbose)
    if biz is None:
        return None, [], err
    # 点位在 data 里，可能是 list 或 {"list":[...]}
    d = biz.get("data")
    if isinstance(d, dict):
        pts = d.get("list") or d.get("points") or d.get("pointList") or []
    elif isinstance(d, list):
        pts = d
    else:
        pts = []
    return biz, pts, None


def main():
    import swcli
    c = swcli.Client()
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 22.981367
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else 116.332141
    unid = int(c.session.get("unid", 0) or 0)
    print("锚点 (%.6f, %.6f)  unid=%s" % (lat, lon, unid))
    biz, pts, err = fetch_points(c, lat, lon, unid)
    if biz is None:
        print("[ERR] %s" % err)
        return 2
    print(json.dumps(biz, ensure_ascii=False, indent=2)[:3000])
    print("-" * 60)
    print("点位数: %d" % len(pts))
    if pts:
        json.dump(pts, open("points_cache.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("[已写出] points_cache.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
