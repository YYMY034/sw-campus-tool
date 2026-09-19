#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rungen/engine.py —— 跑步记录生成引擎

流程:
    1. 时间轴规划: 目标距离/时长 -> 采样点数
    2. 速度曲线: 起步加速 + 稳态波动 + 疲劳衰减 + 红绿灯减速
    3. 路线几何: 打卡点必经 + 样条平滑 + 距离对齐
    4. 逐点推演: 速度积分 -> 真实间距 -> 经纬度/海拔/心率/步频/步幅
    5. 指标汇总 + 每公里分段 + isValidPoint 全量校验
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Sequence

from isvalidpoint import isValidPoint, total_score

from .core import (
    GeoPoint, Split, Checkpoint, TrackRecord, RunnerProfile,
    haversine, bearing, dest_point, add_gps_noise, fmt_pace, fmt_duration,
    DEFAULT_SAMPLE_INTERVAL, IVP_W2, IVP_W3, IVP_W4, IVP_W5,
)
from .route import RouteMode, plan_route, nearest_index, min_tour_length


class RunningGenerator:
    """
    跑步记录生成器。

    用法::

        gen = RunningGenerator(
            start=(30.1234, 104.1234),
            distance_km=3.0,
            start_time="2026-09-15 07:30:00",
            checkpoints=[("一号点", 30.1245, 104.1255),
                         ("二号点", 30.1225, 104.1215)],
        )
        rec = gen.generate()
        print(rec.summary())
    """

    def __init__(self,
                 start: Tuple[float, float],
                 distance_km: float,
                 start_time,
                 checkpoints: Optional[Sequence] = None,
                 profile: Optional[RunnerProfile] = None,
                 mode: str = RouteMode.LOOP,
                 end: Optional[Tuple[float, float]] = None,
                 sample_interval_s: float = DEFAULT_SAMPLE_INTERVAL,
                 seed: Optional[int] = None,
                 target_duration_s: Optional[float] = None,
                 noise_sigma_m: float = 1.6,
                 ):

        self.start = (float(start[0]), float(start[1]))
        self.distance_m = float(distance_km) * 1000.0
        self.mode = mode
        self.end = end
        self.dt = float(sample_interval_s)
        self.noise_sigma_m = noise_sigma_m

        # 打卡点
        self.checkpoints: List[Checkpoint] = []
        for cp in (checkpoints or []):
            if isinstance(cp, Checkpoint):
                self.checkpoints.append(cp)
            elif isinstance(cp, dict):
                self.checkpoints.append(Checkpoint(
                    name=cp.get("name", f"打卡点{len(self.checkpoints)+1}"),
                    lat=float(cp["lat"]), lon=float(cp["lon"]),
                    radius_m=float(cp.get("radius_m", 30.0)),
                ))
            else:
                name, la, lo = cp[0], cp[1], cp[2]
                radius = cp[3] if len(cp) > 3 else 30.0
                self.checkpoints.append(Checkpoint(name, float(la), float(lo), float(radius)))

        # 开始时间
        self.start_time = self._parse_time(start_time)

        self.profile = profile or RunnerProfile()
        self.target_duration_s = target_duration_s

        self.rng = random.Random(seed if seed is not None
                                 else int(self.start_time.timestamp()))

        # 结果缓存
        self.all_points_valid = False
        self.min_point_score = 0
        self.laps = 1
        self.warnings: List[str] = []

    # ------------------------------------------------------------------
    # 时间解析
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_time(t) -> datetime:
        if isinstance(t, datetime):
            dt = t.replace(microsecond=0)
        elif isinstance(t, (int, float)):
            dt = datetime.fromtimestamp(t).replace(microsecond=0)
        else:
            s = str(t).strip()
            fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
                    "%Y/%m/%d %H:%M", "%m-%d %H:%M", "%H:%M:%S", "%H:%M")
            dt = None
            for f in fmts:
                try:
                    dt = datetime.strptime(s, f)
                    if "%Y" not in f:                # 补全年月日
                        now = datetime.now()
                        dt = dt.replace(year=now.year, month=now.month,
                                        day=now.day)
                    dt = dt.replace(microsecond=0)
                    break
                except ValueError:
                    continue
            if dt is None:
                raise ValueError(f"无法解析时间: {t!r}")
        # ★ 硬护栏：起跑时间绝不允许在未来 —— 无论来自 GUI/CLI/配置文件，
        #   一律回退到当前时间（否则轨迹点 ts 与提交体 startTime 落未来，
        #   服务端会判异常并触发风控）。
        now = datetime.now().replace(microsecond=0)
        if dt > now:
            dt = now
        return dt

    # ------------------------------------------------------------------
    # 1. 速度曲线
    # ------------------------------------------------------------------
    def _speed_profile(self, n: int) -> List[float]:
        """
        生成 n 个采样点的瞬时速度 (m/s)。

        构成:
          · 起步加速 (前 4%)
          · 疲劳衰减 (后 40%, 幅度随 fitness 减小)
          · 多周期叠加波动 (步态 / 呼吸 / 地形)
          · 随机微扰
          · 偶发减速 (路口 / 人群 / 上下坡)
          · 移动平均平滑 (防止突变被判定异常)
        """
        base = self.profile.base_speed
        fit = self.profile.fitness
        rng = self.rng
        speeds: List[float] = []

        for i in range(n):
            t = i / max(1, n - 1)

            # 起步加速
            warm = 0.55 + 0.45 * (t / 0.04) if t < 0.04 else 1.0

            # 疲劳衰减
            if t > 0.6:
                fade = 1.0 - (0.10 * (1.0 - fit)) * ((t - 0.6) / 0.4)
            else:
                fade = 1.0

            # 多周期波动
            wave = (1.0
                    + 0.030 * math.sin(2 * math.pi * t * 7.0)
                    + 0.018 * math.sin(2 * math.pi * t * 23.0 + 1.1)
                    + 0.010 * math.sin(2 * math.pi * t * 61.0))

            # 地形起伏 (长周期)
            terrain = 1.0 + 0.025 * math.sin(2 * math.pi * t * 1.8 + 0.4)

            noise = rng.gauss(0, 0.022)

            v = base * warm * fade * wave * terrain * (1 + noise)

            # 偶发减速: 约每 90 点一次
            if rng.random() < 1.0 / 90:
                v *= rng.uniform(0.55, 0.80)

            speeds.append(max(0.6, v))

        # 平滑
        w = max(2, n // 120)
        sm = []
        for i in range(n):
            a, b = max(0, i - w), min(n, i + w + 1)
            sm.append(sum(speeds[a:b]) / (b - a))
        return sm

    # ------------------------------------------------------------------
    # 2. 海拔曲线
    # ------------------------------------------------------------------
    def _make_elevation(self, cum_dists: Sequence[float]) -> List[float]:
        """按累计距离生成海拔剖面 (长坡 + 短起伏 + 微噪声)"""
        rng = self.rng
        base = rng.uniform(15, 130)
        a1 = rng.uniform(2.5, 9.0)     # 长坡幅度
        a2 = rng.uniform(0.8, 2.5)     # 短起伏幅度
        p1 = rng.uniform(0, 2 * math.pi)
        p2 = rng.uniform(0, 2 * math.pi)
        w1 = rng.uniform(700, 1400)    # 长坡波长
        w2 = rng.uniform(180, 320)     # 短起伏波长
        out = []
        for d in cum_dists:
            e = (base
                 + a1 * math.sin(2 * math.pi * d / w1 + p1)
                 + a2 * math.sin(2 * math.pi * d / w2 + p2)
                 + rng.gauss(0, 0.35))
            out.append(e)
        return out

    # ------------------------------------------------------------------
    # 3. 主生成
    # ------------------------------------------------------------------
    def generate(self) -> TrackRecord:
        prof = self.profile
        rng = self.rng

        # --- 可行性检查: 目标距离必须够走完所有打卡点 ---
        # ★ 之前用 "2.2 × 最远打卡距离" 估算, 会严重低估:
        #   打卡点散在四周时, 按方位角排序会绕出 3.4km, 而真实最短
        #   访问顺序只要 1.6km —— 估算不足就会导致"一个卡都打不到"。
        #   这里直接求一遍近似最短访问顺序 (TSP), 得到真实下界。
        if self.checkpoints:
            wps = [(c.lat, c.lon) for c in self.checkpoints]
            need = min_tour_length(self.start, wps, mode=self.mode, end=self.end)
            # 路径必然比直线折线长 (转弯/绕行), 留 12% 余量
            min_needed = need * 1.12
            if self.distance_m < min_needed:
                self.warnings.append(
                    f"目标距离 {self.distance_m/1000:.2f}km 不足以经过全部 "
                    f"{len(self.checkpoints)} 个打卡点 (最短需 "
                    f"{min_needed/1000:.2f}km)。已自动上调到 "
                    f"{min_needed/1000:.2f}km。")
                self.distance_m = min_needed

        # --- 估算总时长 & 采样点数 ---
        if self.target_duration_s:
            est_dur = float(self.target_duration_s)
            self.distance_m = prof.base_speed * est_dur
        else:
            # 留出起步/疲劳余量
            est_dur = self.distance_m / (prof.base_speed * 0.97)

        n = max(20, int(est_dur / self.dt) + 1)

        # --- 速度曲线 ---
        speeds = self._speed_profile(n)

        # --- 路线几何 ---
        waypoints = [(c.lat, c.lon) for c in self.checkpoints]
        coords, self.laps = plan_route(self.start, waypoints, self.distance_m,
                                       mode=self.mode, end=self.end, rng=rng,
                                       noise_sigma_m=self.noise_sigma_m)

        # 几何点数与采样点数对齐: 将几何按弧长重采样到 n 点
        coords = self._align_geometry(coords, n, speeds)

        # ★ 重采样必然"削掉"高曲率路段的弧长 (折返/多圈路线实测损失 4~11%,
        #   因为密集的折返点被抽样跳过了)。这里把对齐后的折线**整体缩放**
        #   回目标长度, 首末点保持不动 —— 这一步之后总距离就精确了。
        coords = self._correct_length(coords, self.distance_m)

        # --- 逐点推演 ---
        # ★★ _walk 会因 min_gap 抬升而**系统性偏长** (2026-09 修复)
        #    `_walk` 在间距小于 `speed*dt*0.55` 时会把该点沿路径外推 ——
        #    这是为了避免"折返处几何打结"导致假速度尖峰。但它有个副作用:
        #    被外推的点成为下一个点的 `prev`, 于是下一段变短、又触发外推,
        #    形成**链式抬升**。实测 1500m 目标走成 1542m (+2.8%), 极端
        #    情况下 (240s/km 快配速 + 密集折返) 能到 +6.2%。
        #    做法: 用 _walk 的实际输出做闭环标定 —— 量出偏长比例, 把
        #    coords 的目标长反向下调 `distance_m / k`, 再走一遍, 迭代
        #    几次即收敛到 <0.3%。
        points, cum = self._walk(coords, speeds, n)
        if not points:
            raise RuntimeError("未生成任何轨迹点")
        for _ in range(5):
            if cum <= 1e-6:
                break
            err = (cum - self.distance_m) / self.distance_m
            if abs(err) <= 0.002:
                break
            # 下一轮的几何目标长: 当前几何长 × (目标/实测)
            cur_geo = sum(haversine(coords[i - 1][0], coords[i - 1][1],
                                    coords[i][0], coords[i][1])
                          for i in range(1, len(coords)))
            nxt = cur_geo * (self.distance_m / cum)
            coords = self._correct_length(coords, max(1.0, nxt))
            points, cum = self._walk(coords, speeds, n)
            if not points:
                raise RuntimeError("未生成任何轨迹点")

        # ★★ 闭环末段"假尖峰"治理 (2026-09 修复)
        #    闭环要求末点严格回到起点, 于是 "几何总长 /(n-1)" 除不尽的
        #    残差全部堆在**最后一段**上: 实测末段 27.7m 而中位 15.3m,
        #    _walk 据此算出 5.54 m/s 的假瞬时速度 (真值 3.03), 步幅被
        #    顶到 189cm —— 单点异常污染整份记录。
        #    做法: 把末段的超出部分按权重摊到前面若干个点上 (末点不动),
        #    使各段间距趋于均匀, 总长几乎不变。
        if self.mode == RouteMode.LOOP and len(coords) >= 6:
            coords = self._flatten_tail_segment(coords)
            points, cum = self._walk(coords, speeds, n)
            if not points:
                raise RuntimeError("未生成任何轨迹点")

        real_dist = points[-1].dist_from_start
        real_dur = (points[-1].ts_ms - points[0].ts_ms) / 1000.0

        # --- 海拔 ---
        eles = self._make_elevation([p.dist_from_start for p in points])
        for p, e in zip(points, eles):
            p.ele = e

        # --- 生理数据 ---
        self._fill_physiology(points, real_dist, real_dur)

        # --- 汇总 ---
        rec = self._build_record(points, real_dist, real_dur)

        # --- 打卡点命中 ---
        rec.checkpoints = self._resolve_checkpoints(points)

        # --- isValidPoint 校验 ---
        self._validate(points)
        rec.all_points_valid = self.all_points_valid
        rec.min_point_score = self.min_point_score
        rec.warnings = list(self.warnings)

        return rec

    # ------------------------------------------------------------------
    def _correct_length(self, coords: List[Tuple[float, float]],
                        target_m: float) -> List[Tuple[float, float]]:
        """
        把重采样后的折线整体缩放回目标长度 (首末点保持不动)。

        为什么需要:
            重采样是"按等弧长取点", 高曲率区段 (折返、绕圈) 的点会被
            跳过, 于是总弧长系统性偏短。闭环路线可以围绕起点缩放;
            开口路线的两端都是硬约束, 所以围绕两端点连线的中点缩放,
            再把残差用幂曲线摊掉 —— 与 route.py 里的做法一致。
        """
        if len(coords) < 3 or target_m <= 0:
            return coords
        cur = sum(haversine(coords[i - 1][0], coords[i - 1][1],
                            coords[i][0], coords[i][1])
                  for i in range(1, len(coords)))
        if cur <= 1e-6:
            return coords

        closed = self.mode == RouteMode.LOOP
        if closed:
            # ★ 闭环必须围绕**环心**缩放, 不能围绕起点:
            #   起点同时也是终点, 以它为不动点缩放会把倒数第二个点
            #   推离终点, 从而在末段撕开一个 2 倍的大缝 (实测末段
            #   28.8m vs 中位 15.8m), _walk 据此算出 5.8m/s 的假速度,
            #   步幅被顶到 195cm。
            #   围绕环心缩放则各点等比例伸缩, 间距分布保持均匀,
            #   最后再把首点平移回原位即可。
            n_pts = len(coords) - 1          # 不含重复的闭合点
            cx = sum(coords[i][0] for i in range(n_pts)) / n_pts
            cy = sum(coords[i][1] for i in range(n_pts)) / n_pts
            k = target_m / cur
            out = [(cx + (a - cx) * k, cy + (b - cy) * k) for a, b in coords]
            # 把首点平移回精确的起点
            la0, lo0 = coords[0]
            d0a, d0b = la0 - out[0][0], lo0 - out[0][1]
            out = [(a + d0a, b + d0b) for a, b in out]
            # ★ 末点**不要**硬钉回起点:
            #   硬钉会在末段撕开一个 2 倍的大缝 (28.8m vs 中位 15.8m),
            #   _walk 据此算出的瞬时速度 5.8m/s 会把步幅顶到 195cm。
            #   这里改成把残差按最大 8% 的间距误差分摊到最后 6 个点上,
            #   既保证闭合 (误差 < 1m, GPS 精度内), 又保持间距均匀。
            gap = haversine(out[-1][0], out[-1][1], out[0][0], out[0][1])
            seg_med = cur / max(1, len(out) - 1)
            if gap > seg_med * 0.5:
                # 把末点朝起点方向"补齐", 但用平滑方式分摊
                brg = bearing(out[-1][0], out[-1][1], out[0][0], out[0][1])
                out[-1] = dest_point(out[-1][0], out[-1][1], brg, gap * 0.5)
            else:
                out[-1] = out[0]
            return out

        # 开口: 围绕两端点中点缩放, 首末点回位
        p0, pN = coords[0], coords[-1]
        cx = (p0[0] + pN[0]) * 0.5
        cy = (p0[1] + pN[1]) * 0.5
        out = list(coords)
        for _ in range(6):
            cur = sum(haversine(out[i - 1][0], out[i - 1][1],
                                out[i][0], out[i][1])
                      for i in range(1, len(out)))
            if cur <= 1e-6:
                break
            k = target_m / cur
            if abs(k - 1.0) < 1e-5:
                break
            out = [(cx + (a - cx) * k, cy + (b - cy) * k) for a, b in out]
            # 首点回位
            d0a, d0b = p0[0] - out[0][0], p0[1] - out[0][1]
            out = [(a + d0a, b + d0b) for a, b in out]
            # 末点回位 (残差按 2.2 次幂摊到全程)
            ra, rb = pN[0] - out[-1][0], pN[1] - out[-1][1]
            if abs(ra) > 1e-12 or abs(rb) > 1e-12:
                m = len(out) - 1
                out = [(a + ra * (i / m) ** 2.2, b + rb * (i / m) ** 2.2)
                       for i, (a, b) in enumerate(out)]
        return out

    # ------------------------------------------------------------------
    def _flatten_tail_segment(self, coords: List[Tuple[float, float]],
                              lookback: int = 8,
                              ) -> List[Tuple[float, float]]:
        """
        闭环: 把**末段**超出中位值的部分, 摊到最后 lookback 个点上。

        问题:
            闭环末点必须等于起点, 于是弧长除不尽的残差全部堆在最后一段。
            实测末段 27.7m vs 中位 15.3m —— `_walk` 据此算出 5.54 m/s 的
            假瞬时速度 (真值 3.03), 步幅冲到 189cm。

        做法:
            1. 取末段前面若干段的**中位间距** med
            2. 若末段 seg_last 明显大于 med (阈值 1.35×), 把超出量
               `excess = seg_last - med` 按线性权重分摊到:
                 · 前面 `lookback` 段的**末顶点** 上 (把它们各自向终点
                   方向"缩"一点, 从而把末段的长度挪走)
            3. 末点本身不动, 所以闭环性保持; 总长变化 < 1m
        """
        out = [tuple(p) for p in coords]
        n = len(out)
        if n < lookback + 3:
            return out

        def seg_at(i):       # 第 i-1 -> i 段的长度
            return haversine(out[i - 1][0], out[i - 1][1],
                             out[i][0], out[i][1])

        segs = [seg_at(i) for i in range(1, n)]
        if not segs:
            return out
        med = sorted(segs)[len(segs) // 2]
        if med <= 1e-6:
            return out
        last = segs[-1]
        if last <= med * 1.35:
            return out

        excess = last - med
        # ★ 直接把"末顶点前的那个点"(out[n-2]) 朝末点方向推进 excess 米,
        #   这样末段就精确变成 med。但推进 out[n-2] 会把倒数第二段
        #   (n-3 -> n-2) **拉长** excess —— 所以再把这个"拉长量"往前
        #   一段传, 如此逐段向前摊, 每段的拉长量按几何衰减 (0.55^i),
        #   传 8 段之后基本归零。末点全程不动, 闭环性不受影响。
        shift = excess
        idx = n - 2
        depth = 0
        while idx > 0 and shift > 0.05 and depth < lookback:
            curp = out[idx]
            nxt = out[idx + 1]
            sl = haversine(curp[0], curp[1], nxt[0], nxt[1])
            if sl <= 1e-9:
                break
            # 向前推进量不能超过 60% 段长, 否则点位会跳过去
            step = min(shift, sl * 0.6)
            f = step / sl
            out[idx] = (curp[0] + (nxt[0] - curp[0]) * f,
                        curp[1] + (nxt[1] - curp[1]) * f)
            # 上一段被拉长了 step, 按衰减系数继续往前传
            shift = step * 0.55
            idx -= 1
            depth += 1
        return out

    # ------------------------------------------------------------------
    def _smooth_closing_gap(self, coords: List[Tuple[float, float]],
                            speeds: List[float],
                            lookback: int = 6) -> List[Tuple[float, float]]:
        """
        把闭环最后一段"多出来的间距"平滑摊到最后 lookback 个点上。

        问题:
            末点必须严格等于起点, 于是"几何总长 / (n-1)" 的除不尽残差
            全部落在最后一段。若末段达到中位间距的 1.8 倍, `_walk` 会
            据此算出 5.8 m/s 的瞬时速度 (平均才 3.0), 步幅随之被顶到
            195cm —— 单点异常会污染整份记录。

        做法:
            1. 算出期望间距 median = 平均(seg)
            2. 若末段 seg_last 明显偏大, 把超出部分按权重摊到
               最后 lookback 段的中间点上 (末点不动, 总长几乎不变)

        仅在闭环时生效 (开口路径的首末点本就是不同位置)。
        """
        if self.mode != RouteMode.LOOP or len(coords) < lookback + 3:
            return coords

        out = [tuple(p) for p in coords]
        m = len(out) - 1
        segs = [haversine(out[i - 1][0], out[i - 1][1], out[i][0], out[i][1])
                for i in range(1, len(out))]
        if not segs:
            return out

        avg = sum(segs) / len(segs)
        if avg <= 1e-6 or segs[-1] <= avg * 1.35:
            return out

        excess = segs[-1] - avg
        rng_pts = min(lookback, m - 1)
        if rng_pts < 2:
            return out

        # 权重: 越靠近末尾权重越大 (平滑钟形)
        w = [i + 1 for i in range(rng_pts)]
        wsum = float(sum(w))
        # 沿路径方向把每个点"往起点方向"挪, 从而补掉末段的超出量
        for j, wi in enumerate(w):
            idx = m - rng_pts + j          # 要调整的点
            if idx <= 0:
                continue
            shift = excess * (wi / wsum)
            la_p, lo_p = out[idx - 1]
            la_c, lo_c = out[idx]
            d = haversine(la_p, lo_p, la_c, lo_c)
            if d < 1e-9:
                continue
            brg = bearing(la_p, lo_p, la_c, lo_c)
            # 目标间距 = 原间距 + 分摊量 (末点方向再压缩)
            new_d = max(0.5, d - shift * 0.5)
            out[idx] = dest_point(la_p, lo_p, brg, new_d)

        # 末点严格回到起点; 末段现在会因为前面点的挪动而略微改变 —— 可接受
        out[-1] = out[0]
        return out

    # ------------------------------------------------------------------
    def _align_geometry(self, coords: List[Tuple[float, float]],
                        n: int, speeds: List[float]) -> List[Tuple[float, float]]:
        """
        把几何点列重采样成 n 点。

        ★ 关键点 (踩过的坑)
            早期实现按 "速度积分比例" 直接定位到几何弧长, 但几何样条的
            相邻点间距并不均匀 (折返路线最短处只有 3m), 而重采样步长
            约 15m —— 每次跨越都会**整段跳过**那段几何。46 次折返累计
            下来, 7000m 的路线只剩 6691m (误差 -5.5%)。

        正确做法:
            1. 先把 "速度积分" 归一成 [0,1] 的**进度比例**
            2. 再用这个比例去乘几何总弧长, 得到目标弧长
            3. 在几何折线上按弧长**线性插值**取点 (不会跳过任何一段)

        这样几何总量严格守恒 (累计弧长单调覆盖 [0, total]),
        同时进度仍然反映速度变化 (快的地方横向跨度大)。
        """
        if len(coords) < 2:
            return [(self.start[0], self.start[1])] * n

        # 累计弧长
        cumlen = [0.0]
        for i in range(1, len(coords)):
            cumlen.append(cumlen[-1] + haversine(coords[i - 1][0], coords[i - 1][1],
                                                 coords[i][0], coords[i][1]))
        total = cumlen[-1] or 1.0

        # 速度积分 -> 归一化进度 [0,1]
        steps = [v * self.dt for v in speeds]
        run = [0.0]
        for s in steps:
            run.append(run[-1] + s)
        run_total = run[-1] or 1.0
        prog = [r / run_total for r in run]

        # 强制首末对齐 (避免累积误差导致末点不落在几何末端)
        prog[0] = 0.0
        prog[-1] = 1.0

        # ★ 消除"末段间距异常":
        #   末点被钉在几何末端, 于是 roundoff 残差全部堆在最后一步上
        #   (实测末段 28.8m, 而中位 15.8m) —— _walk 会据此算出 5.8m/s
        #   的假瞬时速度, 步幅被顶到 195cm。
        #   做法: 把最后 K 步的进度重新线性插值, 使步长与中位数对齐。
        if n > 8:
            step_med = sorted(prog[i] - prog[i - 1]
                              for i in range(1, n))[(n - 1) // 2]
            k = min(8, n - 2)
            last_gap = prog[-1] - prog[-2]
            if step_med > 1e-12 and last_gap > step_med * 1.25:
                # 把 [prog[n-1-k], 1.0] 均分成 k 步
                start_p = prog[-1] - k * step_med
                start_p = max(prog[-1 - k], start_p)
                for j in range(k + 1):
                    prog[n - 1 - k + j] = start_p + (1.0 - start_p) * (j / k)

        out = []
        j = 0
        for i in range(n):
            target = prog[i] * total
            # 单调前进: j 不回退, 保证严格覆盖每一段
            while j < len(cumlen) - 2 and cumlen[j + 1] <= target:
                j += 1
            seg = cumlen[j + 1] - cumlen[j]
            t = 0.0 if seg < 1e-9 else (target - cumlen[j]) / seg
            t = max(0.0, min(1.0, t))
            la = coords[j][0] + (coords[j + 1][0] - coords[j][0]) * t
            lo = coords[j][1] + (coords[j + 1][1] - coords[j][1]) * t
            out.append((la, lo))
        return out

    # ------------------------------------------------------------------
    def _walk(self, coords, speeds, n) -> Tuple[List[GeoPoint], float]:
        """沿几何点列按速度推进, 生成 GeoPoint 列表"""
        points: List[GeoPoint] = []
        cum = 0.0
        prev_la, prev_lo = coords[0]
        t0 = self.start_time

        for i in range(n):
            la, lo = coords[i]

            if i == 0:
                seg = 0.0
            else:
                seg = haversine(prev_la, prev_lo, la, lo)
                # ★ 间距过小 (折返/绕圈处几何打结) 时不要把速度虚增上去:
                #   早期实现是沿朝向"拉开到 speed*dt", 这会造出一个
                #   5.8 m/s 的假尖峰 (平均才 3.0), 进而把步幅推到 195cm。
                #   正确做法: 只把该点**沿路径推进到最小间距** (min_gap),
                #   速度仍由 speed profile 决定 —— 间距与速度解耦。
                min_gap = max(0.4, speeds[i] * self.dt * 0.55)
                if seg < min_gap:
                    brg = (bearing(prev_la, prev_lo, la, lo)
                           if seg > 1e-9 else self.rng.uniform(0, 360))
                    la, lo = dest_point(prev_la, prev_lo, brg, min_gap)
                    seg = haversine(prev_la, prev_lo, la, lo)

            cum += seg
            ts = int((t0 + timedelta(seconds=i * self.dt)).timestamp() * 1000)

            points.append(GeoPoint(
                lat=la, lon=lo, ts_ms=ts, ele=0.0,
                speed=(seg / self.dt if i > 0 else speeds[0]),
                dist_from_start=cum, seg_m=seg,
            ))
            prev_la, prev_lo = la, lo

        return points, cum

    # ------------------------------------------------------------------
    def _fill_physiology(self, points: List[GeoPoint],
                         dist_m: float, dur_s: float) -> None:
        """填充步频 / 步幅 / 心率"""
        prof = self.profile
        rng = self.rng
        avg_v = dist_m / dur_s if dur_s else prof.base_speed

        # --- 步频标定 ---
        # 真实世界: 步频随速度变化, 但比速度变化慢得多。
        # 经验模型 (基于大量跑者数据):
        #     步频 = base_cadence * (v / base_speed) ** 0.16
        # 指数 0.16 意味着速度翻倍, 步频只涨 11% (非常接近实测)
        # 这样在起步低速段 (0.6 m/s) 步频也只会降到约 130, 不会离谱。
        cad_min, cad_max = 128.0, 200.0
        raw = []
        for p in points:
            v = max(0.35, p.speed)                  # 防止除零/负值
            ratio = v / prof.base_speed
            c = prof.base_cadence * (ratio ** 0.16)
            # 个人特征偏移 (每个点独立小抖动)
            c *= (1 + rng.gauss(0, 0.012))
            raw.append(max(cad_min, min(cad_max, c)))

        # 移动平均平滑
        w = 3
        cad = []
        for i in range(len(raw)):
            a, b = max(0, i - w), min(len(raw), i + w + 1)
            cad.append(sum(raw[a:b]) / (b - a))

        # --- 步幅: 由速度 / 步频推导, 并保证下限 ---
        # 真实跑步步幅一般在 80~180cm; 走路约 60~80cm。
        # 起步加速段的极低速会造成步幅过小, 这里做物理下限约束:
        #     步幅(cm) >= 身高 * 0.42  (低速时接近走路步幅)
        stride_floor = prof.height_cm * 0.42
        stride_ceil = prof.height_cm * 1.15
        for p, c in zip(points, cad):
            p.cadence = c
            st = prof.stride_cm(p.speed, c)
            st = max(stride_floor, min(stride_ceil, st))
            p.stride_cm = st

        # --- 心率: 与速度相关 + 时间漂移 ---
        max_hr = prof.max_hr()
        hr_base = 0.62 * max_hr + (avg_v - 2.6) * 11
        drift_total = 0.09 * max_hr
        n = len(points)
        for i, p in enumerate(points):
            t = i / max(1, n - 1)
            h = (hr_base
                 + drift_total * t
                 + (p.speed - avg_v) * 8.5
                 + rng.gauss(0, 2.2))
            p.hr = int(max(95, min(max_hr, h)))

    # ------------------------------------------------------------------
    def _build_record(self, points: List[GeoPoint],
                      dist_m: float, dur_s: float) -> TrackRecord:
        prof = self.profile
        avg_v = dist_m / dur_s if dur_s else 0.0

        cad = [p.cadence for p in points]
        avg_cad = sum(cad) / len(cad)
        avg_stride = sum(p.stride_cm for p in points) / len(points)
        total_steps = int(sum(p.cadence / 60.0 * self.dt for p in points))

        # 爬升 / 下降 (阈值 0.15m 过滤噪声)
        asc = desc = 0.0
        for i in range(1, len(points)):
            d = points[i].ele - points[i - 1].ele
            if d > 0.15:
                asc += d
            elif d < -0.15:
                desc += -d

        hrs = [p.hr for p in points]
        avg_hr = int(sum(hrs) / len(hrs))
        max_hr_v = max(hrs)

        # 卡路里: MET 简化模型
        kcal = prof.weight_kg * (dist_m / 1000.0) * 1.036

        splits = self._build_splits(points)

        avg_pace = dur_s / (dist_m / 1000.0) if dist_m else 0.0
        valid_paces = [s.pace_s_per_km for s in splits if s.pace_s_per_km > 0]
        best_pace = min(valid_paces) if valid_paces else avg_pace

        return TrackRecord(
            start_time=datetime.fromtimestamp(points[0].ts_ms / 1000),
            end_time=datetime.fromtimestamp(points[-1].ts_ms / 1000),
            distance_m=dist_m,
            duration_s=dur_s,
            points=points,
            splits=splits,
            avg_pace_s_per_km=avg_pace,
            best_pace_s_per_km=best_pace,
            avg_speed_mps=avg_v,
            max_speed_mps=max(p.speed for p in points),
            avg_cadence=avg_cad,
            avg_stride_cm=avg_stride,
            total_steps=total_steps,
            total_ascent_m=asc,
            total_descent_m=desc,
            avg_heart_rate=avg_hr,
            max_heart_rate=max_hr_v,
            calories=kcal,
        )

    # ------------------------------------------------------------------
    def _build_splits(self, points: List[GeoPoint]) -> List[Split]:
        """
        按公里切分统计。

        ★ 关键点 (踩过的坑)
            生成出的实际总距离往往不会正好是整数公里 (例如目标 3km
            实际落在 2988m)。若直接用 int(total // 1000) 作为"完整公里数",
            就会把第 3 公里的绝大部分数据丢掉, 变成 "3km 只有 2 段"。

        算法:
            1. 以目标距离 target_m 为准计算应有段数 (而非实际距离)
            2. 对每一段 [k, k+1) km, 用**线性插值**求精确的时间/里程分界
            3. 最后一段若不足 1km, 标记 partial=True
            4. 尾巴过短 (< 3% 公里) 时并入上一段, 避免出现 23m 的碎片段
        """
        splits: List[Split] = []
        if len(points) < 2:
            return splits

        total_d = points[-1].dist_from_start
        if total_d <= 0:
            return splits

        # 以目标距离为基准 (无目标时退回实际距离)
        target_m = self.distance_m if self.distance_m > 0 else total_d
        # 完整公里数: 以目标为准, 但也参考实际距离
        n_full = int(round(min(target_m, total_d) / 1000.0))
        if n_full < 1:
            n_full = int(total_d // 1000.0)
        # 保证不超过实际数据能支撑的段数
        n_full = max(0, min(n_full, int(total_d // 1000.0) + 1))

        tail_d = total_d - n_full * 1000.0

        # 尾巴极短 -> 并入最后一段 (把最后一段的终点延到实际末尾)
        merge_tail = (n_full >= 1 and 0.0 <= tail_d < 30.0)

        for km in range(1, n_full + 1):
            d_a = (km - 1) * 1000.0
            d_b = km * 1000.0
            if merge_tail and km == n_full:
                d_b = total_d                       # 吸收尾巴
            if d_b > total_d:
                d_b = total_d
            a = self._index_at_distance(points, d_a)
            b = self._index_at_distance(points, d_b)
            if b > a:
                s = self._make_split(km, points, a, b)
                # 补上插值带来的端点精确化 (可选, 保持数据自洽)
                s.distance_m = max(s.distance_m, 1e-6)
                splits.append(s)

        # 真正的尾巴段 (>= 30m)
        if not merge_tail and tail_d >= 30.0:
            a = self._index_at_distance(points, n_full * 1000.0)
            b = len(points) - 1
            if b > a:
                s = self._make_split(n_full + 1, points, a, b)
                s.partial = True
                splits.append(s)

        return splits

    @staticmethod
    def _index_at_distance(points: List[GeoPoint], d: float) -> int:
        """返回累计距离首次 >= d 的点下标 (线性扫描, 点数少时够快)"""
        for i, p in enumerate(points):
            if p.dist_from_start >= d:
                return i
        return len(points) - 1

    def _make_split(self, km: int, points: List[GeoPoint],
                    a: int, b: int) -> Split:
        seg = points[a:b + 1]
        d = seg[-1].dist_from_start - seg[0].dist_from_start
        t = (seg[-1].ts_ms - seg[0].ts_ms) / 1000.0
        pace = t / (d / 1000.0) if d > 0 else 0.0
        asc = 0.0
        for i in range(1, len(seg)):
            dd = seg[i].ele - seg[i - 1].ele
            if dd > 0.15:
                asc += dd
        return Split(
            km=km,
            distance_m=d,
            time_s=t,
            pace_s_per_km=pace,
            avg_cadence=sum(p.cadence for p in seg) / len(seg),
            avg_stride_cm=sum(p.stride_cm for p in seg) / len(seg),
            elev_gain_m=asc,
            avg_hr=int(sum(p.hr for p in seg) / len(seg)),
        )

    # ------------------------------------------------------------------
    def _resolve_checkpoints(self, points: List[GeoPoint]) -> List[dict]:
        """计算每个打卡点是否被轨迹命中"""
        out = []
        for cp in self.checkpoints:
            best_i, best_d = -1, float("inf")
            for i, p in enumerate(points):
                d = haversine(p.lat, p.lon, cp.lat, cp.lon)
                if d < best_d:
                    best_d, best_i = d, i
            cp.hit_index = best_i if best_d <= cp.radius_m else -1
            cp.hit_dist_m = best_d
            if cp.hit_index >= 0:
                cp.hit_time = datetime.fromtimestamp(
                    points[cp.hit_index].ts_ms / 1000).strftime("%H:%M:%S")
            out.append(cp.to_dict())
        return out

    # ------------------------------------------------------------------
    def _validate(self, points: List[GeoPoint]) -> None:
        """
        ★ 对所有点位跑 isValidPoint 校验。
        参数: w2=1 (有效采集点), w3=20 (高档), w4=1, w5=0
        s0 = 瞬时速度, s1 = 与上一点的间距
        """
        ok_all = True
        min_score = 10 ** 9
        for i, p in enumerate(points):
            s0 = p.speed
            s1 = p.seg_m if i > 0 else max(1.0, p.speed * self.dt)
            if not isValidPoint(IVP_W2, s0, s1, IVP_W3, IVP_W4, IVP_W5):
                ok_all = False
            sc = total_score(IVP_W2, s0, s1, IVP_W3)
            min_score = min(min_score, sc)
        self.all_points_valid = ok_all
        self.min_point_score = min_score if min_score < 10 ** 9 else 0

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def export_json(self, rec: TrackRecord, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(rec.to_json())

    def export_gpx(self, rec: TrackRecord, path: str) -> None:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<gpx version="1.1" creator="RunningGenerator"',
                 '     xmlns="http://www.topografix.com/GPX/1/1">',
                 '  <trk><name>Running</name><trkseg>']
        for p in rec.points:
            t = datetime.fromtimestamp(p.ts_ms / 1000).strftime("%Y-%m-%dT%H:%M:%SZ")
            lines.append(f'    <trkpt lat="{p.lat:.7f}" lon="{p.lon:.7f}">'
                         f'<ele>{p.ele:.1f}</ele><time>{t}</time></trkpt>')
        lines += ['  </trkseg></trk>', '</gpx>']
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def export_csv(self, rec: TrackRecord, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("idx,timestamp,time,lat,lon,ele,speed_mps,cadence,stride_cm,hr,dist_m,seg_m\n")
            for i, p in enumerate(rec.points):
                f.write(f"{i},{p.ts_ms},"
                        f"{datetime.fromtimestamp(p.ts_ms/1000).strftime('%H:%M:%S')},"
                        f"{p.lat:.7f},{p.lon:.7f},{p.ele:.2f},{p.speed:.4f},"
                        f"{p.cadence:.1f},{p.stride_cm:.1f},{p.hr},"
                        f"{p.dist_from_start:.2f},{p.seg_m:.3f}\n")

    def export_all(self, rec: TrackRecord, outdir: str, prefix: str = "run") -> dict:
        """一次性导出 json / gpx / csv, 返回路径字典"""
        import os
        os.makedirs(outdir, exist_ok=True)
        paths = {
            "json": os.path.join(outdir, f"{prefix}.json"),
            "gpx": os.path.join(outdir, f"{prefix}.gpx"),
            "csv": os.path.join(outdir, f"{prefix}.csv"),
        }
        self.export_json(rec, paths["json"])
        self.export_gpx(rec, paths["gpx"])
        self.export_csv(rec, paths["csv"])
        return paths


# ============================================================
# 便捷入口
# ============================================================
def generate_record(start_lat: float, start_lon: float, distance_km: float,
                    start_time=None, checkpoints=None,
                    pace_s_per_km: float = 330.0, mode: str = RouteMode.LOOP,
                    end=None, seed: Optional[int] = None, cadence: float = 172.0,
                    height_cm: float = 172.0, weight_kg: float = 65.0,
                    age: int = 22, fitness: float = 0.5,
                    sample_interval_s: float = DEFAULT_SAMPLE_INTERVAL,
                    ) -> TrackRecord:
    """一行生成完整跑步记录

    :param end: 点到点模式的终点 (纬度, 经度); 其他模式忽略
    """
    if start_time is None:
        start_time = datetime.now().replace(microsecond=0)
    prof = RunnerProfile(base_pace_s_per_km=pace_s_per_km,
                         base_cadence=cadence,
                         height_cm=height_cm,
                         weight_kg=weight_kg,
                         age=age,
                         fitness_level=fitness)
    gen = RunningGenerator(
        start=(start_lat, start_lon),
        distance_km=distance_km,
        start_time=start_time,
        checkpoints=checkpoints,
        profile=prof,
        mode=mode,
        end=end,
        seed=seed,
        sample_interval_s=sample_interval_s,
    )
    return gen.generate()
