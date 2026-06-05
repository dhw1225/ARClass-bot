"""
Arcaea 计分反算模块
根据曲目 notes 数和得分反推"错数"（每1 FAR = 1错数，每1 LOST = 2错数）
"""

import json
import math
from pathlib import Path
from typing import Optional

# 加载曲目数据
_SONGS_PATH = Path(__file__).parent / "songs.json"


class SongDB:
    """曲目数据库，支持按名称或索引查找"""

    def __init__(self):
        with open(_SONGS_PATH, "r", encoding="utf-8") as f:
            self._songs: list[dict] = json.load(f)
        self._name_index: dict[str, int] = {}
        self._name_difficulty_index: dict[tuple[str, str], int] = {}
        for i, song in enumerate(self._songs):
            canonical_name = song["name"].lower()
            difficulty = song["difficulty"].upper()
            self._name_index[canonical_name] = i
            self._name_difficulty_index[(canonical_name, difficulty)] = i
            for alias in song.get("aliases", []):
                alias_name = str(alias).strip().lower()
                if alias_name:
                    self._name_difficulty_index[(alias_name, difficulty)] = i

    def get_by_name(self, name: str) -> Optional[dict]:
        """按曲名查找，大小写不敏感"""
        return (
            self._songs[self._name_index[name.lower()]]
            if name.lower() in self._name_index
            else None
        )

    def get_by_name_and_difficulty(self, name: str, difficulty: str) -> Optional[dict]:
        """按曲名和难度查找，大小写不敏感"""
        key = (name.lower(), difficulty.upper())
        return (
            self._songs[self._name_difficulty_index[key]]
            if key in self._name_difficulty_index
            else None
        )

    def get_by_index(self, index: int) -> Optional[dict]:
        """按 JSON 列表下标查找"""
        if 0 <= index < len(self._songs):
            return self._songs[index]
        return None

    def find(self, identifier: str | int) -> Optional[dict]:
        """
        智能查找：传入 int 按索引，传入 str 按曲名
        """
        if isinstance(identifier, int):
            return self.get_by_index(identifier)
        elif isinstance(identifier, str):
            return self.get_by_name(identifier)
        return None

    @property
    def songs(self) -> list[dict]:
        return self._songs

    def __len__(self) -> int:
        return len(self._songs)


def calculate_faults(notes: int, score: int) -> Optional[dict]:
    """
    根据 notes 数和分数反推错数及 max pure 个数。

    公式：score = floor(10000000 / (2 * notes) * (2 * notes - faults)) + max_pure
    其中 0 <= faults <= 2 * notes, 0 <= max_pure <= notes

    参数：
        notes: 曲目 note 总数
        score: 游戏分数

    返回：
        {"faults": int, "max_pure": int}  或  None（无法解析时）
    """
    if notes <= 0:
        return None
    two_n = 2 * notes
    for faults in range(two_n + 1):
        base = math.floor(10000000 // two_n * (two_n - faults))
        # 整除是为了性能，但公式中有 10000000/(2N) 的除法再 floor，用 // 有时会丢失精度
        # 更准确的方式：
        base = math.floor(10000000 * (two_n - faults) / two_n)
        max_pure = score - base
        if 0 <= max_pure <= notes:
            return {"faults": faults, "max_pure": max_pure}
    return None


def effective_faults(
    notes: int,
    faults: int,
    max_pure: int,
    *,
    strict_faults: bool = False,
    strict_multiplier: int = 1,
) -> int:
    """Return the challenge fault value for normal or strict HP rules."""
    if not strict_faults:
        return faults
    small_pure = notes - max_pure
    return small_pure + strict_multiplier * faults


# 兼容旧解释：错数就是 faults 本身
# 若需拆分为 (far, lost)：faults = far + 2*lost, far+lost <= notes
# 反解不唯一，故仅返回总错数


# --- 高层接口 ---

_song_db: Optional[SongDB] = None


def get_db() -> SongDB:
    """获取全局曲目数据库单例"""
    global _song_db
    if _song_db is None:
        _song_db = SongDB()
    return _song_db


def query(song: str | int, score: int, difficulty: Optional[str] = None) -> Optional[dict]:
    """
    传入曲目标识（曲名 或 JSON 下标）和分数，返回错数信息。

    返回示例：
        {
            "song": "Tempestissimo",
            "difficulty": "BYD",
            "notes": 1540,
            "score": 10001540,
            "faults": 0,
            "max_pure": 1540
        }
    """
    db = get_db()
    entry = (
        db.get_by_name_and_difficulty(song, difficulty)
        if isinstance(song, str) and difficulty
        else db.find(song)
    )
    if entry is None:
        return None
    notes = entry["notes"]
    result = calculate_faults(notes, score)
    if result is None:
        return None
    return {
        "song": entry["name"],
        "difficulty": entry["difficulty"],
        "notes": notes,
        "score": score,
        "faults": result["faults"],
        "max_pure": result["max_pure"],
    }


# --- 简单 CLI 测试 ---
if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        song_id = sys.argv[1]
        score = int(sys.argv[2])
        # 尝试将数字字符串转为 int
        try:
            song_id = int(song_id)
        except ValueError:
            pass
        res = query(song_id, score)
        if res:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print("无法找到曲目或无法解析分数")
    else:
        # 默认跑一个测试
        db = SongDB()
        for entry in db.songs[:3]:
            notes = entry["notes"]
            perfect = math.floor(10000000 / (2 * notes) * (2 * notes)) + notes
            res = query(entry["name"], perfect)
            print(json.dumps(res, ensure_ascii=False))
