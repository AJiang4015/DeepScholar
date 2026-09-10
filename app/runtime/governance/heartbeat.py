"""P2-2 S2 — HeartbeatWriter（Runtime Health Telemetry 写入器）。

决策/规范依据：`P2-2_SPEC_v2.md` Rev 2.2 §4（Heartbeat Model）、§5.2/§13（配置）；
`DECISION.md` D-Phase2-P2-2-001（health plane；**非** lease/fencing）、-004（首拍时机）、
-011（确认次数，与 writer 无关）、-015（PG 写约束：节流 + 抖动，不引连接池/依赖）。

边界（MUST）：
- 本模块**只**经 `app.runtime.governance.health_store` 写 `governance_runtime_health`；
  **MUST NOT** 触碰 `governance_tasks` 的任何 lifecycle 字段（LA-1 / invariant I2）；
- 心跳写失败 **fail-open**：任何异常一律吞掉 + 计数 + 日志，**绝不**传播到任务执行控制路径；
- throttle 为 **per-task 进程内状态**，执行结束由调用方 `forget(task_id)` 释放（S3 接线）；
- 无连接池、无新依赖；写入沿用 health_store 的单语句短事务。

S2 范围：本模块自身不接线。首拍 / watchdog tick 的调用点属 S3（controller additive-only 接线）；
模式（`RUNTIME_RECLAIM_MODE=off`）门控与 lifespan 装配属 S6；stale/reclaim 属 S4/S5。

clock 可注入（invariant I10）：
- `monotonic_fn` 驱动 throttle 窗口（默认真实时钟）；
- `utcnow_fn` 提供写入时间戳（默认 UTC now，`timespec="seconds"` 与仓库时间列约定一致）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.runtime.governance import health_store

logger = logging.getLogger("deepsearch.runtime.governance.heartbeat")

#: env 键（P2-2 Spec Rev 2.2 §13）
HEARTBEAT_INTERVAL_ENV = "RUNTIME_HEARTBEAT_INTERVAL"
HEARTBEAT_JITTER_ENV = "RUNTIME_HEARTBEAT_JITTER"

DEFAULT_INTERVAL_SECONDS = 5.0
DEFAULT_JITTER_PERCENT = 20.0
MIN_INTERVAL_SECONDS = 1.0
MAX_INTERVAL_SECONDS = 3600.0
MAX_JITTER_PERCENT = 50.0


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HeartbeatConfig:
    """心跳写入配置（env 可覆盖；默认值 = Spec §13 冻结默认）。

    - `interval_seconds`：写节流窗口（默认 5s，下限 1s、上限 3600s，越界钳制）；
    - `jitter_percent`：相位抖动比例（默认 20%，范围 0–50%，越界钳制）；
    - `enabled`：由**装配方**注入（S6：`RUNTIME_RECLAIM_MODE=off` ⇒ False）；
      本类不从 env 读取模式，避免与 S4 `ReclaimConfig` 的模式解析形成双源。
    """

    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    jitter_percent: float = DEFAULT_JITTER_PERCENT
    enabled: bool = True

    @classmethod
    def from_env(cls, env: Optional[dict[str, str]] = None) -> "HeartbeatConfig":
        """从 env 解析（缺省 `os.environ`）；非法/越界 → 回退默认或钳制 + 诊断日志。"""
        source = os.environ if env is None else env
        interval = _parse_float(
            source.get(HEARTBEAT_INTERVAL_ENV),
            default=DEFAULT_INTERVAL_SECONDS,
            key=HEARTBEAT_INTERVAL_ENV,
        )
        interval = _clamp(
            interval,
            low=MIN_INTERVAL_SECONDS,
            high=MAX_INTERVAL_SECONDS,
            key=HEARTBEAT_INTERVAL_ENV,
        )
        jitter = _parse_float(
            source.get(HEARTBEAT_JITTER_ENV),
            default=DEFAULT_JITTER_PERCENT,
            key=HEARTBEAT_JITTER_ENV,
        )
        jitter = _clamp(
            jitter, low=0.0, high=MAX_JITTER_PERCENT, key=HEARTBEAT_JITTER_ENV
        )
        return cls(interval_seconds=interval, jitter_percent=jitter, enabled=True)

    def phase_seconds(self, task_id: str) -> float:
        """确定性相位（0 ≤ phase ≤ interval × jitter%）。

        使用 `hashlib.sha256(task_id)`（**非** 内置 `hash()`，避免 PYTHONHASHSEED 随机化）
        → 同一 task_id 在任何进程/任何运行中产生同一相位；不同 task 分散，避免齐拍。
        """
        span = self.interval_seconds * (self.jitter_percent / 100.0)
        if span <= 0:
            return 0.0
        digest = hashlib.sha256(str(task_id).encode("utf-8")).digest()
        fraction = int.from_bytes(digest[:8], "big") / float(1 << 64)
        return round(span * fraction, 6)


def _parse_float(raw: Optional[str], *, default: float, key: str) -> float:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s 取值非法（%r）→ 回退默认 %s", key, raw, default)
        return default


def _clamp(value: float, *, low: float, high: float, key: str) -> float:
    if value < low:
        logger.warning("%s 低于下限（%s < %s）→ 钳制", key, value, low)
        return low
    if value > high:
        logger.warning("%s 高于上限（%s > %s）→ 钳制", key, value, high)
        return high
    return value


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------
@dataclass
class _ThrottleState:
    last_mono: float
    beats: int = 1


class HeartbeatWriter:
    """per-task 节流的 health plane 写入器（单一职责：写心跳）。

    用法（S3 接线）：governed 执行 handle 注册成功后调用首拍（`force=True`）；
    watchdog tick 每轮调用一次（内部按 interval + 相位节流）；执行结束时 `forget(task_id)`。
    """

    def __init__(
        self,
        store: Any,
        *,
        config: Optional[HeartbeatConfig] = None,
        monotonic_fn: Optional[Callable[[], float]] = None,
        utcnow_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._store = store
        self._config = config or HeartbeatConfig()
        self._monotonic = monotonic_fn or time.monotonic
        self._utcnow = utcnow_fn or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._state: dict[str, _ThrottleState] = {}
        self._write_failures = 0

    # -- read-only introspection（诊断 / 测试） ---------------------------
    @property
    def config(self) -> HeartbeatConfig:
        return self._config

    @property
    def write_failures(self) -> int:
        return self._write_failures

    def tracked_tasks(self) -> list[str]:
        """当前持有 throttle 状态（= 执行中）的 task_id 列表。"""
        with self._lock:
            return sorted(self._state)

    def local_beats(self, task_id: str) -> int:
        """进程内已写心跳次数（诊断用；!= DB beat_count 时说明发生了跨进程/重启）。"""
        with self._lock:
            state = self._state.get(task_id)
            return state.beats if state is not None else 0

    # -- write path ------------------------------------------------------
    def beat(
        self,
        *,
        task_id: str,
        thread_id: str,
        run_id: Optional[str],
        owner_instance: str,
        force: bool = False,
    ) -> bool:
        """尝试写一次心跳；返回是否**成功写入**（被节流或失败 → False）。

        语义：
        - `enabled=False`（`off` 模式）→ 直接 False，不写、不建状态；
        - 首拍（该 task 无 throttle 状态）→ 立即写（D13：执行起点首拍，不等待相位）；
        - 后续：仅当 `monotonic() >= last_mono + interval + phase(task_id)` 才写（相位确定性）；
        - `force=True` → 跳过节流判定（仍受 enabled 约束）；
        - **fail-open**：任何写入异常 → 计数 + 日志 + 返回 False，绝不抛出。
        """
        if not self._config.enabled:
            return False

        now_mono = self._monotonic()
        with self._lock:
            state = self._state.get(task_id)
            if state is not None and not force:
                due_at = (
                    state.last_mono
                    + self._config.interval_seconds
                    + self._config.phase_seconds(task_id)
                )
                if now_mono < due_at:
                    return False

        # 写操作在锁外（短 DB 调用；避免持锁跨 IO）
        now_iso = self._utcnow().astimezone(timezone.utc).isoformat(timespec="seconds")
        try:
            written = health_store.upsert_heartbeat(
                self._store,
                task_id=task_id,
                thread_id=thread_id,
                run_id=run_id,
                owner_instance=owner_instance,
                now_iso=now_iso,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open：健康平面不得影响执行
            with self._lock:
                self._write_failures += 1
            logger.warning("heartbeat 写入失败（task %s，fail-open）：%s", task_id, exc)
            return False

        if not written:
            return False
        with self._lock:
            state = self._state.get(task_id)
            if state is None:
                self._state[task_id] = _ThrottleState(last_mono=now_mono)
            else:
                state.last_mono = now_mono
                state.beats += 1
        return True

    def forget(self, task_id: str) -> None:
        """释放该 task 的 throttle 状态（执行结束时调用；不删 DB 健康行 —— 清理属 scanner）。"""
        with self._lock:
            self._state.pop(task_id, None)
