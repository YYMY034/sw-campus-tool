#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rungen/core.py —— 跑步记录生成引擎
====================================

设计目标: 生成"像真人跑出来"的完整跑步数据集, 用于运动世界校园的
          打卡记录提交。

输入:
    · 起点经纬度
    · 打卡点列表 (必经过的地点)
    · 目标距离 / 目标时长 (二选一, 另一个自动推导)
    · 开始时间
    · 跑者生理画像 (配速基线 / 步频 / 身高体重 / 体能水平)

输出:
    · 逐点 GPS 轨迹 (lat/lon/高程/时间戳/瞬时速度/累计距离)
    · 汇总指标 (距离 / 时长 / 平均配速 / 最佳配速 / 平均速度 / 最高速度)
    · 生理数据 (步频 / 步幅 / 步数 / 心率 / 卡路里)
    · 爬升下降
    · 每公里分段 (配速 / 步频 / 步幅 / 爬升 / 心率)
    · 所有点位的 isValidPoint 校验结果

★ 关键设计: 所有点位保证通过 libswsport.so 的 isValidPoint (见 isvalidpoint.py)
   使用参数组合 w2=1, w3=20, s0>0  ->  总分 70~100, 远超 65 阈值
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Sequence

from isvalidpoint import isValidPoint, total_score

# ============================================================
# 常量
# ============================================================
R_EARTH = 6371000.0          # 地球半径 (米)
DEFAULT_SAMPLE_INTERVAL = 5  # 采样间隔 (秒), 与常见跑步 App 一致

# isValidPoint 参数 (最优组合)
IVP_W2 = 1                   # 点位类型 = 有效采集点  -> w9 = 30
IVP_W3 = 20                  # 点位序号高档        -> w8 = 30
IVP_W4 = 1                   # 标志位 A 必须 & 0xff != 0
IVP_W5 = 0                   # 标志位 B 必须 & 0xff == 0


# ============================================================
# 地理工具
# ============================================================
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点球面大圆距离 (米)"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """起点->终点方位角 (度, 正北为 0, 顺时针)"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = (math.cos(p1) * math.sin(p2)
         - math.sin(p1) * math.cos(p2) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def dest_point(lat: float, lon: float, brg_deg: float, dist_m: float) -> Tuple[float, float]:
    """从 (lat,lon) 沿方位角 brg_deg 前进 dist_m 米后的坐标"""
    d = dist_m / R_EARTH
    b = math.radians(brg_deg)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d)
                   + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def add_gps_noise(lat: float, lon: float, sigma_m: float = 1.6,
                  rng: Optional[random.Random] = None) -> Tuple[float, float]:
    """叠加 GPS 漂移噪声 (真实手机定位有 1~5m 抖动)"""
    r = rng or random
    rr = abs(r.gauss(0, sigma_m))
    b = r.uniform(0, 360)
    return dest_point(lat, lon, b, rr)


# ============================================================
# 格式化
# ============================================================
def fmt_pace(sec_per_km: float) -> str:
    """配速格式化: 330 -> 5'30\""""
    if not sec_per_km or sec_per_km <= 0:
        return "--'--\""
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m}'{s:02d}\""


def fmt_duration(sec: float) -> str:
    """时长格式化: 3725 -> 1:02:05"""
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_pace(text: str) -> float:
    """解析配速字符串 -> 秒/公里. 支持 5'30\" / 5:30 / 330"""
    text = text.strip().replace('"', '').replace("'", ':')
    if ':' in text:
        parts = text.split(':')
        return float(parts[0]) * 60 + float(parts[1])
    return float(text)


# ============================================================
# 数据模型
# ============================================================
@dataclass
class GeoPoint:
    """单个轨迹点"""
    lat: float
    lon: float
    ts_ms: int                  # 绝对毫秒时间戳
    ele: float = 0.0            # 海拔 (米)
    speed: float = 0.0          # 瞬时速度 (m/s)
    cadence: float = 0.0        # 步频 (步/分钟)
    stride_cm: float = 0.0      # 步幅 (厘米)
    hr: int = 0                 # 心率
    dist_from_start: float = 0.0  # 距起点累计距离 (米)
    seg_m: float = 0.0          # 与上一点的间距 (米) —— isValidPoint 的 s1


@dataclass
class Split:
    """每公里分段"""
    km: int
    distance_m: float
    time_s: float
    pace_s_per_km: float
    avg_cadence: float
    avg_stride_cm: float
    elev_gain_m: float
    avg_hr: int
    partial: bool = False       # 是否不足 1 公里的尾巴

    def to_dict(self):
        d = asdict(self)
        d["time"] = fmt_duration(self.time_s)
        d["pace"] = fmt_pace(self.pace_s_per_km)
        d["distance_m"] = round(self.distance_m, 1)
        d["time_s"] = round(self.time_s, 1)
        d["pace_s_per_km"] = round(self.pace_s_per_km, 1)
        d["avg_cadence"] = round(self.avg_cadence, 1)
        d["avg_stride_cm"] = round(self.avg_stride_cm, 1)
        d["elev_gain_m"] = round(self.elev_gain_m, 1)
        return d


@dataclass
class Checkpoint:
    """打卡点"""
    name: str
    lat: float
    lon: float
    radius_m: float = 30.0      # 有效打卡半径
    hit_index: int = -1         # 命中的轨迹点下标
    hit_dist_m: float = -1.0    # 与打卡点的最小距离
    hit_time: str = ""          # 打卡时刻

    def to_dict(self):
        return {
            "name": self.name,
            "lat": round(self.lat, 7),
            "lon": round(self.lon, 7),
            "radius_m": self.radius_m,
            "hit": self.hit_index >= 0,
            "hit_dist_m": round(self.hit_dist_m, 2) if self.hit_dist_m >= 0 else None,
            "hit_time": self.hit_time,
        }


@dataclass
class TrackRecord:
    """一次完整跑步记录"""
    start_time: datetime
    end_time: datetime
    distance_m: float
    duration_s: float

    points: List[GeoPoint] = field(default_factory=list)
    splits: List[Split] = field(default_factory=list)
    checkpoints: List[dict] = field(default_factory=list)

    # 汇总指标
    avg_pace_s_per_km: float = 0.0
    best_pace_s_per_km: float = 0.0
    avg_speed_mps: float = 0.0
    max_speed_mps: float = 0.0

    avg_cadence: float = 0.0
    avg_stride_cm: float = 0.0
    total_steps: int = 0

    total_ascent_m: float = 0.0
    total_descent_m: float = 0.0
    avg_heart_rate: int = 0
    max_heart_rate: int = 0
    calories: float = 0.0

    # 校验
    all_points_valid: bool = True
    min_point_score: int = 0

    # 生成过程中的提示 (例如目标距离不够跑到打卡点, 已被自动上调)
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "开始时间": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "结束时间": self.end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "总距离": f"{self.distance_m / 1000:.2f} km",
            "总时长": fmt_duration(self.duration_s),
            "平均配速": fmt_pace(self.avg_pace_s_per_km) + " /km",
            "最佳配速": fmt_pace(self.best_pace_s_per_km) + " /km",
            "平均速度": f"{self.avg_speed_mps:.2f} m/s ({self.avg_speed_mps * 3.6:.1f} km/h)",
            "最高速度": f"{self.max_speed_mps:.2f} m/s ({self.max_speed_mps * 3.6:.1f} km/h)",
            "平均步频": f"{self.avg_cadence:.1f} 步/分",
            "平均步幅": f"{self.avg_stride_cm:.1f} cm",
            "总步数": f"{self.total_steps} 步",
            "累计爬升": f"{self.total_ascent_m:.1f} m",
            "累计下降": f"{self.total_descent_m:.1f} m",
            "平均心率": f"{self.avg_heart_rate} bpm",
            "最高心率": f"{self.max_heart_rate} bpm",
            "消耗热量": f"{self.calories:.0f} kcal",
            "轨迹点数": len(self.points),
            "点位校验": "全部通过" if self.all_points_valid else "★ 存在不通过点位",
            "最低得分": self.min_point_score,
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "metrics": {
                "distance_m": round(self.distance_m, 2),
                "duration_s": round(self.duration_s, 2),
                "avg_pace_s_per_km": round(self.avg_pace_s_per_km, 2),
                "best_pace_s_per_km": round(self.best_pace_s_per_km, 2),
                "avg_speed_mps": round(self.avg_speed_mps, 4),
                "max_speed_mps": round(self.max_speed_mps, 4),
                "avg_cadence": round(self.avg_cadence, 2),
                "avg_stride_cm": round(self.avg_stride_cm, 2),
                "total_steps": self.total_steps,
                "total_ascent_m": round(self.total_ascent_m, 2),
                "total_descent_m": round(self.total_descent_m, 2),
                "avg_heart_rate": self.avg_heart_rate,
                "max_heart_rate": self.max_heart_rate,
                "calories": round(self.calories, 1),
            },
            "splits": [s.to_dict() for s in self.splits],
            "checkpoints": self.checkpoints,
            "warnings": self.warnings,
            "points": [
                {
                    "i": i,
                    "ts": p.ts_ms,
                    "time": datetime.fromtimestamp(p.ts_ms / 1000).strftime("%H:%M:%S"),
                    "lat": round(p.lat, 7),
                    "lon": round(p.lon, 7),
                    "ele": round(p.ele, 2),
                    "speed": round(p.speed, 4),
                    "cadence": round(p.cadence, 1),
                    "stride_cm": round(p.stride_cm, 1),
                    "hr": p.hr,
                    "dist": round(p.dist_from_start, 2),
                    "seg_m": round(p.seg_m, 3),
                }
                for i, p in enumerate(self.points)
            ],
            "validation": {
                "all_pass": self.all_points_valid,
                "min_score": self.min_point_score,
                "params": {"w2": IVP_W2, "w3": IVP_W3, "w4": IVP_W4, "w5": IVP_W5},
            },
        }

    def to_json(self, indent=2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ============================================================
# 跑者画像
# ============================================================
class RunnerProfile:
    """
    跑者生理参数 —— 决定配速/步频/步幅的基线。

    经验关系:
        配速 6'00"/km -> 步频 ~168
        配速 5'00"/km -> 步频 ~178
        配速 4'00"/km -> 步频 ~186
        步幅(cm) = 速度(m/s) / (步频/60) * 100
        成年男性步幅 ≈ 身高 × 0.6~0.8 (跑步时更大)
    """

    def __init__(self,
                 base_pace_s_per_km: float = 330.0,   # 5'30"/km
                 base_cadence: float = 172.0,
                 height_cm: float = 172.0,
                 weight_kg: float = 65.0,
                 age: int = 22,
                 fitness_level: float = 0.5,          # 0~1, 越高波动越小
                 ):
        self.base_pace = base_pace_s_per_km
        self.base_cadence = base_cadence
        self.height_cm = height_cm
        self.weight_kg = weight_kg
        self.age = age
        self.fitness = max(0.0, min(1.0, fitness_level))

    @property
    def base_speed(self) -> float:
        """基线速度 m/s"""
        return 1000.0 / self.base_pace

    def max_hr(self) -> int:
        """最大心率估算 (Tanaka)"""
        return int(208 - 0.7 * self.age)

    def stride_cm(self, speed_mps: float, cadence: float) -> float:
        """步幅(厘米) = 速度 / 步频(每秒) * 100"""
        if cadence <= 0:
            return 0.0
        return speed_mps / (cadence / 60.0) * 100.0
