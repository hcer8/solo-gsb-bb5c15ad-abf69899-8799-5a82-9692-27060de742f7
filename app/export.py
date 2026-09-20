"""复盘包导出：主持人在任意时刻按当前事件水位生成离线 ZIP。

一致性保证（“生成期间到达的新事件不得混入”）：
- 调用方持有 state_lock，且本模块只读取 id <= 水位 W 的事件；
  状态表（groups/cards/receipts/answers）在锁内读取，其内容不可能新于 W。
- 导出动作本身不落事件、不改状态，可在 idle/paused/running 任意时刻执行。

虚拟时间还原：
- clock(started/paused/resumed) 与 reset 事件把演练切成若干“运行段”，
  其余事件按其墙钟 created_at 映射到所在轮次的虚拟时钟毫秒：
  运行中 = 段起点冻结值 + 墙钟差；暂停/待机 = 当前冻结值。
- 同一虚拟时刻的多个事件天然按事件 ID 升序（遍历即 ID 序），
  回放页直接使用该顺序，不做任何二次重排。

剔除敏感凭证：
- share_created / share_revoked / share_expired 事件整体剔除（含观察 token）；
- group_added 等载荷中的 join_code 字段剔除；
- 房间快照不含 rooms.code，分组快照只保留 id/name；observer_shares 表不导出。
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone

from .replay_page import build_replay_html

EXPORT_FORMAT = "inject-debrief/1"
# 无论载荷里出现什么，这些键都不允许进入复盘包
SECRET_KEYS = ("join_code", "token", "code")


def iso_utc(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def room_elapsed_ms(room: sqlite3.Row) -> int:
    """与 app.main.room_elapsed_ms 同口径：running 时加算本次运行段。"""
    import time as _time

    elapsed = room["elapsed_ms"]
    if room["status"] == "running" and room["last_resume_at"] is not None:
        elapsed += int((_time.time() - room["last_resume_at"]) * 1000)
    return max(0, elapsed)


def clean_payload(type_: str, payload: dict) -> dict | None:
    """事件载荷脱敏：分享事件整体丢弃；其余事件剔除凭证字段。"""
    if type_.startswith("share_"):
        return None
    out = dict(payload)
    for key in SECRET_KEYS:
        out.pop(key, None)
    return out


def annotate_timeline(rows: list[sqlite3.Row]) -> tuple[list[dict], list[dict], list[dict]]:
    """把事件流按 ID 升序遍历，为每个事件标注轮次 round 与虚拟时间 vtime_ms。

    round = 截至该事件已经发生过的 started 次数（0 = 首次开始前的编排阶段）；
    reset 结束当前轮次，其后到下一次 started 之间的编排事件 round 不变、
    vtime = 0，回放页按 ID 边界把它们归入下一轮的前置状态。

    返回 (标注后的事件列表, 时钟动作列表, 轮次元数据列表)。
    """
    events: list[dict] = []
    clock_actions: list[dict] = []
    starts_seen = 0
    phase = "idle"        # idle | running | paused
    seg_start = None      # 当前运行段的墙钟起点（秒）
    base_elapsed = 0      # 当前运行段起点的虚拟时钟毫秒
    frozen = 0            # 暂停/待机时的冻结值
    start_ids: list[int] = []
    reset_ids: list[int] = []
    round_max: dict[int, int] = {}

    def note_max(r: int, v: int) -> None:
        if v > round_max.get(r, 0):
            round_max[r] = v

    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            payload = {}
        typ = row["type"]
        wall = row["created_at"]
        rnd = starts_seen
        vtime = frozen

        if typ == "clock":
            action = payload.get("action")
            if action == "started":
                starts_seen += 1
                rnd = starts_seen
                phase = "running"
                seg_start = wall
                base_elapsed = 0
                frozen = 0
                vtime = 0
                start_ids.append(row["id"])
            elif action == "paused":
                frozen = int(payload.get("elapsed_ms", frozen))
                phase = "paused"
                seg_start = None
                vtime = frozen
            elif action == "resumed":
                frozen = int(payload.get("elapsed_ms", frozen))
                base_elapsed = frozen
                seg_start = wall
                phase = "running"
                vtime = frozen
        elif typ == "reset":
            phase = "idle"
            frozen = 0
            seg_start = None
            vtime = 0
            reset_ids.append(row["id"])
        elif phase == "running" and seg_start is not None:
            vtime = int(base_elapsed + (wall - seg_start) * 1000)

        vtime = max(0, int(vtime))
        note_max(rnd, vtime)
        cleaned = clean_payload(typ, payload)
        entry = {
            "id": row["id"],
            "type": typ,
            "payload": cleaned if cleaned is not None else payload,  # share 事件最后仍会被过滤
            "created_at": wall,
            "created_at_iso": iso_utc(wall),
            "round": rnd,
            "vtime_ms": vtime,
            "phase": phase,
            "_dropped": cleaned is None,
        }
        events.append(entry)
        if typ in ("clock", "reset"):
            p = payload
            clock_actions.append({
                "event_id": row["id"],
                "kind": typ,
                "action": p.get("action", typ),
                "status": p.get("status"),
                "elapsed_ms": p.get("elapsed_ms", 0) if typ == "clock" else 0,
                "created_at": wall,
                "created_at_iso": iso_utc(wall),
                "round": rnd,
                "vtime_ms": vtime,
            })

    # 轮次配对：第 r 次 started 到其后（下一次 started 之前）的 reset 为止
    rounds: list[dict] = []
    for i, sid in enumerate(start_ids, start=1):
        next_start = start_ids[i] if i < len(start_ids) else None
        rid = next((x for x in reset_ids if sid < x and (next_start is None or x < next_start)), None)
        rounds.append({
            "index": i,
            "start_event_id": sid,
            "reset_event_id": rid,
            "max_vtime_ms": round_max.get(i, 0),
        })
    return events, clock_actions, rounds


def collect_bundle(conn: sqlite3.Connection, room: sqlite3.Row) -> dict:
    """在锁内汇总水位 W 下的全部复盘数据（已脱敏）。"""
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    watermark = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM events WHERE room_id = ?", (room["id"],)
    ).fetchone()["m"]

    rows = conn.execute(
        "SELECT * FROM events WHERE room_id = ? AND id <= ? ORDER BY id",
        (room["id"], watermark),
    ).fetchall()
    events, clock_actions, rounds = annotate_timeline(rows)
    # 分享事件含观察 token：整体剔除，不进入包内任何数据文件
    events = [e for e in events if not e.pop("_dropped")]

    groups = [
        {"id": r["id"], "name": r["name"]}  # 刻意不含 join_code
        for r in conn.execute(
            "SELECT id, name FROM groups WHERE room_id = ? ORDER BY id", (room["id"],)
        )
    ]
    cards = [
        {
            "id": r["id"],
            "seq": r["seq"],
            "trigger_seconds": r["trigger_seconds"],
            "title": r["title"],
            "content": r["content"],
            "group_id": r["group_id"],
            "revealed": bool(r["revealed"]),
            "revealed_at_ms": r["revealed_at_ms"],
            "timed_out": bool(r["timed_out"]),
            "answers_locked": bool(r["answers_locked"]),
            "depends_on_card_id": r["depends_on_card_id"],
            "gate_timeout_seconds": r["gate_timeout_seconds"],
        }
        for r in conn.execute(
            "SELECT * FROM cards WHERE room_id = ? ORDER BY trigger_seconds, seq", (room["id"],)
        )
    ]
    receipts = [
        {
            "card_id": r["card_id"],
            "group_id": r["group_id"],
            "created_at": r["created_at"],
            "created_at_iso": iso_utc(r["created_at"]),
        }
        for r in conn.execute(
            "SELECT * FROM receipts WHERE room_id = ? ORDER BY card_id, group_id", (room["id"],)
        )
    ]

    # 答复版本轨迹：按 (卡片, 组) 收集 answer_updated 事件（含被 reset 清空的历史轮次）
    trails: dict[tuple[int, int], list[dict]] = {}
    for e in events:
        if e["type"] != "answer_updated":
            continue
        p = e["payload"]
        trails.setdefault((p["card_id"], p["group_id"]), []).append({
            "event_id": e["id"],
            "revision": p["revision"],
            "body": p["body"],
            "updated_at": p.get("updated_at"),
            "updated_at_iso": iso_utc(p.get("updated_at")),
            "round": e["round"],
            "vtime_ms": e["vtime_ms"],
        })
    current = {
        (r["card_id"], r["group_id"]): {
            "revision": r["revision"],
            "body": r["body"],
            "updated_at": r["updated_at"],
            "updated_at_iso": iso_utc(r["updated_at"]),
        }
        for r in conn.execute("SELECT * FROM answers WHERE room_id = ?", (room["id"],)
    )}
    answers = []
    for (cid, gid), hist in sorted(trails.items()):
        answers.append({
            "card_id": cid,
            "group_id": gid,
            # reset 后表中已无当前行，但历史轨迹仍保留
            "current": current.get((cid, gid)),
            "trajectory": hist,
        })

    elapsed_now = room_elapsed_ms(room)
    # 进行中/暂停中的最后一轮：滑块右端要能到达导出瞬间的虚拟时钟
    if rounds and rounds[-1]["reset_event_id"] is None:
        rounds[-1]["max_vtime_ms"] = max(rounds[-1]["max_vtime_ms"], elapsed_now)
        rounds[-1]["end_phase"] = room["status"]
    for rnd in rounds:
        rnd.setdefault("end_phase", "reset")

    return {
        "format": EXPORT_FORMAT,
        "generated_at": generated_at,
        "watermark": watermark,
        "room": {
            "id": room["id"],  # 不含房间码
            "name": room["name"],
            "status_at_export": room["status"],
            "elapsed_ms_at_export": elapsed_now,
            "created_at": room["created_at"],
            "created_at_iso": iso_utc(room["created_at"]),
        },
        "groups": groups,
        "cards": cards,
        "receipts": receipts,
        "answers": answers,
        "clock_actions": clock_actions,
        "events": events,
        "rounds": rounds,
        "counts": {
            "groups": len(groups),
            "cards": len(cards),
            "revealed_cards": sum(1 for c in cards if c["revealed"]),
            "receipts": len(receipts),
            "answers_current": len(current),
            "answer_revisions": sum(len(a["trajectory"]) for a in answers),
            "clock_actions": len(clock_actions),
            "events": len(events),
            "rounds": len(rounds),
        },
    }


README_TEMPLATE = """情景注入台 · 复盘包
================================

导出时间（UTC）：{generated_at}
事件水位：#{watermark}（本包只含 ID 不超过该水位的事件；导出期间到达的新事件不会混入）
演练房间：{room_name}
导出时虚拟时钟：{elapsed}（{status}）

如何打开回放
------------
双击（或浏览器中打开） replay.html 即可，无需联网、无需服务端：
- 顶部滑块可拖动虚拟时间，逐毫秒还原当时的房间编排、已揭示卡片、
  回执、处置答复（含版本号）与时钟状态；
- 同一虚拟时刻的事件按事件 ID 稳定排序；
- 多轮演练（开始 / 重置 / 再开始）可用页签切换轮次；
- 支持播放按钮按 1×/2×/4×/8× 速度自动回放。

包内文件
--------
replay.html                  自包含离线回放页（数据已内嵌，不访问网络）
data/room.json               房间信息与导出元数据（无房间码）
data/groups.json             参与组编排（仅 id 与名称，无加入码）
data/cards.json              全部卡片编排、揭示状态与回执门槛配置
data/receipts.json           水位时刻的现存回执
data/answers.json            各组答复的当前版本与完整版本轨迹
data/clock-actions.json      时钟动作（开始/暂停/继续/重置）及虚拟时间
data/events.json             脱敏后的完整事件流（含轮次与虚拟时间标注）

已剔除的敏感信息
----------------
- 房间码（主持人凭证）
- 组加入码（参与者凭证）
- 观察 token 及全部 share_* 观察席事件

空演练同样可以导出：本包在没有任何参与组、卡片与事件时也能生成，
replay.html 会显示空状态页面。
"""


def build_debrief_zip(conn: sqlite3.Connection, room: sqlite3.Row) -> tuple[bytes, int]:
    """生成复盘包 ZIP 字节串。必须在 state_lock 下调用。"""
    bundle = collect_bundle(conn, room)

    def jdump(obj) -> str:
        return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)

    readme = README_TEMPLATE.format(
        generated_at=bundle["generated_at"],
        watermark=bundle["watermark"],
        room_name=bundle["room"]["name"] or "未命名演练",
        elapsed=_fmt_elapsed(bundle["room"]["elapsed_ms_at_export"]),
        status={
            "idle": "待机", "running": "进行中", "paused": "已暂停",
        }.get(bundle["room"]["status_at_export"], bundle["room"]["status_at_export"]),
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("replay.html", build_replay_html(bundle))
        zf.writestr("README.txt", readme)
        zf.writestr("data/room.json", jdump({
            "format": bundle["format"],
            "generated_at": bundle["generated_at"],
            "watermark": bundle["watermark"],
            "room": bundle["room"],
            "counts": bundle["counts"],
            "rounds": bundle["rounds"],
        }))
        zf.writestr("data/groups.json", jdump(bundle["groups"]))
        zf.writestr("data/cards.json", jdump(bundle["cards"]))
        zf.writestr("data/receipts.json", jdump(bundle["receipts"]))
        zf.writestr("data/answers.json", jdump(bundle["answers"]))
        zf.writestr("data/clock-actions.json", jdump(bundle["clock_actions"]))
        zf.writestr("data/events.json", jdump(bundle["events"]))
    return buf.getvalue(), bundle["watermark"]


def _fmt_elapsed(ms: int) -> str:
    s = max(0, int(ms // 1000))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
