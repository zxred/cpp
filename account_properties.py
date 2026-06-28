# -*- coding: utf-8 -*-
"""
Реализация account_properties.py по правилам нативного C++ движка BigWorld.
Позволяет передавать stats, inventory, serverSettings клиенту без использования pickle.
"""

from __future__ import annotations

import struct
from typing import Any
from account_serializer import (
    BinaryWriter, 
    pack_fixed_dict, 
    pack_string, 
    pack_uint32, 
    pack_int64, 
    pack_int32
)


def build_receive_properties_element(entity_id: int) -> tuple[bytes, int]:
    """
    Формирует пакет для клиентского метода receiveProperties(stats, inventory, serverSettings)
    без использования pickle.
    """
    # 1. Задаём нативные данные
    stats_val = {
        "credits": 150000,
        "gold": 2500,
        "freeXP": 12000,
        "crystal": 500,
    }
    stats_schema = {
        "credits": "INT64",
        "gold": "INT32",
        "freeXP": "INT32",
        "crystal": "INT32",
    }

    inventory_val = {
        "items": [], # Пустой массив предметов
    }
    inventory_schema = {
        "items": "ARRAY",
    }

    server_settings_val = {
        "isB2P": False,
        "roaming": (0, "RU", "RU"),
        "wgcg": {},
        "premiumPlusBonus": {},
        "regional_settings": {
            "starting_day_of_a_new_day": 0,
            "starting_time_of_a_new_day": 0
        },
    }
    server_settings_schema = {
        "isB2P": "BOOL",
        "premiumPlusBonus": "FIXED_DICT_EMPTY",
        "regional_settings": "REGIONAL_SETTINGS",
        "roaming": "ROAMING_TUPLE",
        "wgcg": "FIXED_DICT_EMPTY",
    }

    # 2. Пакуем данные в поток DataStream
    writer = BinaryWriter()
    
    # Сначала ID сущности (4 байта LE)
    writer.write_uint32(entity_id)

    # Пакуем stats как FIXED_DICT
    stats_bytes = pack_fixed_dict(stats_val, stats_schema)
    writer.write_blob(stats_bytes)

    # Пакуем inventory как FIXED_DICT
    inventory_bytes = pack_fixed_dict(inventory_val, inventory_schema)
    writer.write_blob(inventory_bytes)

    # Пакуем serverSettings как FIXED_DICT
    server_settings_bytes = pack_fixed_dict(server_settings_val, server_settings_schema)
    writer.write_blob(server_settings_bytes)

    payload = writer.get_bytes()

    # 3. Оборачиваем в элемент метода (Mercury element)
    # Метод receiveProperties в WoT обычно имеет индекс (method_id), пусть будет 18 (или соответствующий)
    method_id = 18
    # Структура вызова: [entity_id: 4B] [method_id: 1B] [аргументы]
    content = struct.pack('<I', entity_id) + bytes([method_id]) + payload[4:]

    # Собираем Mercury элемент переменной длины: [0x85 : msg_id] [length : 2B LE] [content]
    msg_id = 0x85 # Типичный ID сообщения EntityMethod
    element = bytes([msg_id]) + struct.pack('<H', len(content)) + content

    return element, len(element)


def send_receive_properties(addr, ch, entity_id: int):
    """
    Отправляет пакет receiveProperties на указанный канал.
    Служит точкой интеграции для альтернативных сценариев.
    """
    element, _ = build_receive_properties_element(entity_id)
    return element
