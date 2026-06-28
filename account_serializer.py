from __future__ import annotations

import os
import struct
import pickle
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from typing import Any


# ============================================================================
# 1. Метаданные типов BigWorld
# ============================================================================

TYPE_SIZES: dict[str, int] = {
    "UINT8": 1, "INT8": 1, "BOOL": 1,
    "UINT16": 2, "INT16": 2,
    "UINT32": 4, "INT32": 4, "FLOAT32": 4, "OBJECT_ID": 4,
    "UINT64": 8, "INT64": 8, "FLOAT64": 8, "DB_ID": 8,
    "VECTOR2": 8, "VECTOR3": 12,

    # Переменные размеры
    "STRING": -1, "BLOB": -1, "PYTHON": -1, "ARRAY": -1, "FIXED_DICT": -1,
}

@dataclass(frozen=True)
class PropertyMeta:
    name: str
    type_name: str
    flags: str = "BASE_AND_CLIENT"

    @property
    def stream_size(self) -> int:
        try:
            return TYPE_SIZES[self.type_name]
        except KeyError as exc:
            raise KeyError(f"Unknown BigWorld type: {self.type_name!r}") from exc


# ============================================================================
# 2. Динамический парсинг Account.def (для извлечения только CLIENT-visible свойств)
# ============================================================================

FALLBACK_CLIENT_PROPERTIES: list[PropertyMeta] = [
    PropertyMeta("incarnationID", "UINT64", "BASE_AND_CLIENT"),
    PropertyMeta("name", "STRING", "BASE_AND_CLIENT"),
    PropertyMeta("initialServerSettings", "PYTHON", "BASE_AND_CLIENT"),
]

def _find_project_root() -> str | None:
    """Ищет корень проекта по характерным папкам: entity_defs, arena_defs и т.д."""
    markers = ['entity_defs', 'arena_defs', 'component_defs', 'space_defs', 'item_defs']
    d = os.path.abspath('.')
    for _ in range(5):
        for marker in markers:
            if os.path.isdir(os.path.join(d, marker)):
                return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


# Папки, где живут .def файлы
DEF_DIRS = ['entity_defs', 'arena_defs', 'component_defs', 'space_defs', 'item_defs']


def _find_def_file(def_name: str) -> str | None:
    """
    Рекурсивный поиск .def файла среди всех DEF_DIRS.
    Приоритет: прямое совпадение в родительской папке > рекурсия глубже.
    Всегда возвращает XML-файл, пропуская бинарные.
    """
    root = _find_project_root()
    if not root:
        return None

    # Собираем все кандидаты
    candidates = []
    for def_dir in DEF_DIRS:
        base = os.path.join(root, def_dir)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            if def_name in filenames:
                full = os.path.join(dirpath, def_name)
                candidates.append(full)

    # Приоритет: entity_defs > arena_defs > component_defs > space_defs > item_defs
    dir_priority = {d: i for i, d in enumerate(DEF_DIRS)}
    def sort_key(path):
        rel = os.path.relpath(path, root)
        top_dir = rel.split(os.sep)[0] if os.sep in rel else rel
        depth = rel.count(os.sep)
        return (dir_priority.get(top_dir, 99), depth)

    candidates.sort(key=sort_key)

    for cand in candidates:
        with open(cand, 'rb') as f:
            header = f.read(16)
        if header.startswith(b'<?xml'):
            return cand

    return None


def load_client_properties(def_name: str = 'Account.def') -> list[PropertyMeta]:
    """
    Рекурсивно ищет .def файл среди entity_defs/arena_defs/component_defs/space_defs/item_defs.
    Парсит XML, возвращает только CLIENT-visible свойства.
    """
    def_path = _find_def_file(def_name)

    if not def_path:
        print(f"[SERIALIZER] {def_name} not found. Using hardcoded fallback.")
        return FALLBACK_CLIENT_PROPERTIES

    try:
        with open(def_path, 'r', encoding='utf-8') as f:
            xml_data = f.read()

        # Очистка пространств имён, чтобы XML-парсер Python не падал
        xml_data = xml_data.replace('<xmlns:xsi>', '<xmlns_xsi>')
        xml_data = xml_data.replace('</xmlns:xsi>', '</xmlns_xsi>')
        xml_data = xml_data.replace('<xsi:noNamespaceSchemaLocation>', '<xsi_noNamespaceSchemaLocation>')
        xml_data = xml_data.replace('</xsi:noNamespaceSchemaLocation>', '</xsi_noNamespaceSchemaLocation>')

        root = ET.fromstring(xml_data)
        properties_el = root.find('Properties')
        if properties_el is None:
            return FALLBACK_CLIENT_PROPERTIES

        client_props = []
        for prop in properties_el:
            flags_el = prop.find('Flags')
            flags = flags_el.text.strip() if (flags_el is not None and flags_el.text) else "BASE"
            
            # Только свойства, имеющие CLIENT в флагах, уходят клиенту при создании сущности!
            if 'CLIENT' in flags.upper():
                type_el = prop.find('Type')
                if type_el is not None:
                    if len(type_el) > 0:
                        type_str = ET.tostring(type_el, encoding='unicode').strip()
                    else:
                        type_str = type_el.text.strip() if type_el.text else "PYTHON"
                else:
                    type_str = "PYTHON"
                
                client_props.append(PropertyMeta(name=prop.tag, type_name=type_str, flags=flags))
                
        if not client_props:
            return FALLBACK_CLIENT_PROPERTIES
            
        return client_props

    except Exception as e:
        print(f"[SERIALIZER] [WARNING] Ошибка при парсинге Account.def ({e}). Используется клиентский fallback.")
        return FALLBACK_CLIENT_PROPERTIES


# ============================================================================
# 3. Алгоритм сортировки BigWorld (Stable Sort / PropertiesSortHelper)
# ============================================================================

def get_sort_key(prop: Any) -> tuple[int, int, str]:
    size = getattr(prop, 'stream_size', -1)
    # Возвращаем кортеж: (категория, размер, имя_для_алфавитной_сортировки)
    if size >= 0:
        return (0, size, prop.name)
    return (1, -size, prop.name)

def get_sorted_properties(properties: list[Any]) -> list[Any]:
    # Сортировка по возрастанию размера для фиксированных типов, 
    # а для одинаковых размеров или переменных типов — строго по алфавиту!
    return sorted(properties, key=get_sort_key)


# ============================================================================
# 4. Модульные хелпер-функции для паковки типов BigWorld (C++ нативный DataStream)
# ============================================================================

def pack_int8(val: int) -> bytes: return struct.pack('<b', val)
def pack_uint8(val: int) -> bytes: return struct.pack('<B', val)
def pack_int16(val: int) -> bytes: return struct.pack('<h', val)
def pack_uint16(val: int) -> bytes: return struct.pack('<H', val)
def pack_int32(val: int) -> bytes: return struct.pack('<i', val)
def pack_uint32(val: int) -> bytes: return struct.pack('<I', val)
def pack_int64(val: int) -> bytes: return struct.pack('<q', val)
def pack_uint64(val: int) -> bytes: return struct.pack('<Q', val)
def pack_float32(val: float) -> bytes: return struct.pack('<f', val)
def pack_float64(val: float) -> bytes: return struct.pack('<d', val)
def pack_vector2(val: tuple[float, float]) -> bytes: return struct.pack('<2f', *val)
def pack_vector3(val: tuple[float, float, float]) -> bytes: return struct.pack('<3f', *val)

# Нативный упаковщик префикса длины BigWorld (packed_u24)
def pack_bw_length(length: int) -> bytes:
    if length < 254:
        return bytes([length]) # 1 байт длины [cite: 9]
    else:
        # Маркер 0xFF + 3 байта длины в Little-Endian (итого 4 байта) 
        return bytes([0xFF]) + struct.pack('<I', length)[:3]

def pack_string(val: str, len_type: str = "packed_int") -> bytes:
    encoded = val.encode('utf-8')
    if len_type == "packed_int":
        return pack_bw_length(len(encoded)) + encoded
    elif len_type == "uint8":
        return struct.pack('<B', len(encoded)) + encoded
    elif len_type == "uint16":
        return struct.pack('<H', len(encoded)) + encoded
    return encoded

def pack_blob(val: bytes) -> bytes:
    return pack_bw_length(len(val)) + val

def pack_python(val: Any) -> bytes:
    # 1. Если данные пришли от server_stub в виде pickle-байтов, 
    # «на лету» десериализуем их в чистый Python-объект
    if isinstance(val, bytes) and val.startswith(b'\x80\x02'):
        try:
            val = pickle.loads(val)
        except Exception:
            pass

    # 2. Если это словарь настроек сервера (initialServerSettings), 
    # упаковываем его строго по C++ схеме BigWorld (FIXED_DICT структуры)
    if isinstance(val, dict) and ('isB2P' in val or 'roaming' in val or 'regional_settings' in val):
        isB2P = val.get('isB2P', False)
        
        reg_settings = val.get('regional_settings', {})
        # Извлекаем параметры времени (учитываем возможные варианты ключей)
        starting_day = reg_settings.get('starting_day_of_a_new_day', reg_settings.get('starting_day', 0))
        starting_time = reg_settings.get('starting_time_of_a_new_day', reg_settings.get('starting_time', 0))
        
        roaming = val.get('roaming', (0, 'RU', 'RU'))
        
        # Собираем строгое бинарное тело DataStream (ровно 19 байт)
        payload = bytearray()
        payload.extend(struct.pack('<B', 1 if isB2P else 0))       # 1 байт (BOOL)
        payload.extend(struct.pack('<i', starting_day))           # 4 байта (INT32)
        payload.extend(struct.pack('<i', starting_time))          # 4 байта (INT32)
        payload.extend(struct.pack('<i', roaming[0]))             # 4 байта (INT32)
        
        # Внутренние строки структуры в BigWorld идут с 1-байтовым префиксом длины (uint8)
        r1 = roaming[1].encode('utf-8') if isinstance(roaming[1], str) else roaming[1]
        payload.extend(struct.pack('<B', len(r1)) + r1)           # 1 байт длины + "RU"
        
        r2 = roaming[2].encode('utf-8') if isinstance(roaming[2], str) else roaming[2]
        payload.extend(struct.pack('<B', len(r2)) + r2)           # 1 байт длины + "RU"
        
        # Возвращаем результат, снабдив его общим сетевым префиксом длины BigWorld
        return pack_bw_length(len(payload)) + bytes(payload)

    # 3. Фолбек-заглушка для любых других PYTHON-свойств, если они объявятся
    if isinstance(val, bytes):
        return pack_bw_length(len(val)) + val
    pickle_bytes = pickle.dumps(val, protocol=2)
    return pack_bw_length(len(pickle_bytes)) + pickle_bytes
    
    # Иначе пакуем объект в чистый бинарный pickle протокола 2
    pickle_bytes = pickle.dumps(val, protocol=2)
    return pack_bw_length(len(pickle_bytes)) + pickle_bytes

def pack_string(val: str, len_type: str = "uint16") -> bytes:
    """Паковка строки с префиксом длины."""
    data = val.encode('utf-8')
    length = len(data)
    if len_type == "uint8":
        prefix = pack_uint8(length)
    elif len_type == "uint16":
        prefix = pack_uint16(length)
    elif len_type in ("packed_int", "packed_u24"):
        # BigWorld packed_u24 (wg-toolkit write_packed_u24)
        if length <= 254:
            prefix = pack_uint8(length)
        else:
            prefix = pack_uint8(0xFF) + struct.pack('<I', length & 0xFFFFFF)[:3]
    else:
        raise ValueError(f"Unknown len_type: {len_type}")
    return prefix + data

def pack_array(arr: list, elem_type: str = "PYTHON", len_type: str = "uint8") -> bytes:
    """Паковка массива элементов одного типа с префиксом размера."""
    length = len(arr)
    if len_type == "uint8":
        prefix = pack_uint8(length)
    elif len_type == "uint16":
        prefix = pack_uint16(length)
    elif len_type in ("packed_int", "packed_u24"):
        if length <= 254:
            prefix = pack_uint8(length)
        else:
            prefix = pack_uint8(0xFF) + struct.pack('<I', length & 0xFFFFFF)[:3]
    else:
        raise ValueError(f"Unknown len_type: {len_type}")
    
    body = bytearray()
    for elem in arr:
        body.extend(pack_value_generic(elem, elem_type))
    return prefix + bytes(body)

def pack_fixed_dict(val: dict, schema: dict[str, str]) -> bytes:
    """Паковка FIXED_DICT по алфавитному порядку полей."""
    body = bytearray()
    sorted_keys = sorted(schema.keys())
    for key in sorted_keys:
        field_type = schema[key]
        field_val = val.get(key)
        if field_val is None:
            field_val = get_default_value_generic(field_type)
        body.extend(pack_value_generic(field_val, field_type))
    return bytes(body)


def get_default_value_generic(type_name: str) -> Any:
    if type_name in ("UINT8", "INT8", "UINT16", "INT16", "UINT32", "INT32", "UINT64", "INT64", "DB_ID", "OBJECT_ID"):
        return 0
    elif type_name == "BOOL":
        return False
    elif type_name in ("FLOAT32", "FLOAT64"):
        return 0.0
    elif type_name in ("STRING", "BLOB", "PYTHON"):
        return "" if type_name == "STRING" else b""
    elif type_name == "VECTOR2":
        return (0.0, 0.0)
    elif type_name == "VECTOR3":
        return (0.0, 0.0, 0.0)
    elif type_name == "ARRAY":
        return []
    elif type_name == "FIXED_DICT":
        return {}
    return None


def pack_value_generic(val: Any, type_name: str) -> bytes:
    if type_name == "UINT8": return pack_uint8(val)
    elif type_name == "INT8": return pack_int8(val)
    elif type_name == "BOOL": return pack_uint8(1 if val else 0)
    elif type_name == "UINT16": return pack_uint16(val)
    elif type_name == "INT16": return pack_int16(val)
    elif type_name == "UINT32": return pack_uint32(val)
    elif type_name == "INT32": return pack_int32(val)
    elif type_name == "OBJECT_ID": return pack_uint32(val)
    elif type_name == "UINT64": return pack_uint64(val)
    elif type_name == "INT64": return pack_int64(val)
    elif type_name == "DB_ID": return pack_int64(val)
    elif type_name == "FLOAT32": return pack_float32(val)
    elif type_name == "FLOAT64": return pack_float64(val)
    elif type_name == "VECTOR2": return pack_vector2(val)
    elif type_name == "VECTOR3": return pack_vector3(val)
    elif type_name == "STRING": return pack_string(val, len_type="uint16")
    elif type_name == "BLOB":
        if isinstance(val, str): val = val.encode('utf-8')
        return pack_string(val.decode('latin1') if isinstance(val, bytes) else val, len_type="packed_int")
    elif type_name == "PYTHON":
        return pack_python_object_generic(val)
    elif type_name == "ARRAY":
        return pack_array(val, "PYTHON")
    elif type_name == "FIXED_DICT":
        return pack_fixed_dict(val, {})
    else:
        # Сложные вложенные структуры
        if type_name == "INITIAL_SERVER_SETTINGS":
            return pack_fixed_dict(val, {
                "isB2P": "BOOL",
                "premiumPlusBonus": "FIXED_DICT_EMPTY",
                "regional_settings": "REGIONAL_SETTINGS",
                "roaming": "ROAMING_TUPLE",
                "wgcg": "FIXED_DICT_EMPTY",
            })
        elif type_name == "REGIONAL_SETTINGS":
            return pack_fixed_dict(val, {
                "starting_day_of_a_new_day": "INT32",
                "starting_time_of_a_new_day": "INT32",
            })
        elif type_name == "ROAMING_TUPLE":
            # Кортеж фиксированного типа
            body = bytearray()
            body.extend(pack_int32(val[0]))
            body.extend(pack_string(val[1], len_type="uint8"))
            body.extend(pack_string(val[2], len_type="uint8"))
            return bytes(body)
        elif type_name == "FIXED_DICT_EMPTY":
            return b''
        raise NotImplementedError(f"Generic pack for custom type {type_name} not implemented!")


def pack_python_object_generic(val: Any) -> bytes:
    """Рекурсивная бескомпиляционная сериализация Python объектов в DataStream."""
    if val is None:
        return pack_uint8(0)
    
    if isinstance(val, dict):
        if "isB2P" in val and "roaming" in val:
            packed_dict = pack_value_generic(val, "INITIAL_SERVER_SETTINGS")
        else:
            # Обычный словарь пакуем как FIXED_DICT по алфавиту
            body = bytearray()
            sorted_keys = sorted(val.keys())
            for key in sorted_keys:
                field_val = val[key]
                body.extend(pack_python_object_generic(field_val))
            packed_dict = bytes(body)
        length_prefix = pack_uint8(len(packed_dict)) if len(packed_dict) <= 254 else (pack_uint8(0xFF) + struct.pack('<I', len(packed_dict) & 0xFFFFFF)[:3])
        return length_prefix + packed_dict
        
    elif isinstance(val, (list, tuple)):
        body = bytearray()
        for elem in val:
            body.extend(pack_python_object_generic(elem))
        packed_arr = bytes(body)
        length_prefix = pack_uint8(len(packed_arr)) if len(packed_arr) <= 254 else (pack_uint8(0xFF) + struct.pack('<I', len(packed_arr) & 0xFFFFFF)[:3])
        return length_prefix + packed_arr
        
    elif isinstance(val, str):
        packed_str = val.encode('utf-8')
        length_prefix = pack_uint8(len(packed_str)) if len(packed_str) <= 254 else (pack_uint8(0xFF) + struct.pack('<I', len(packed_str) & 0xFFFFFF)[:3])
        return length_prefix + packed_str
        
    elif isinstance(val, bool):
        return pack_uint8(1 if val else 0)
        
    elif isinstance(val, int):
        if val.bit_length() <= 31:
            return pack_int32(val)
        else:
            return pack_int64(val)
            
    elif isinstance(val, float):
        return pack_float32(val)
        
    elif isinstance(val, bytes):
        length_prefix = pack_uint8(len(val)) if len(val) <= 254 else (pack_uint8(0xFF) + struct.pack('<I', len(val) & 0xFFFFFF)[:3])
        return length_prefix + val
        
    else:
        raise NotImplementedError(f"Cannot pack python type {type(val)} recursively!")


# ============================================================================
# 5. Классы BinaryReader и BinaryWriter для потоковой работы
# ============================================================================

class BinaryReader:
    def __init__(self, data: bytes):
        self._buffer = BytesIO(data)

    def tell(self) -> int:
        return self._buffer.tell()

    def remaining(self) -> int:
        pos = self._buffer.tell()
        self._buffer.seek(0, 2)
        end = self._buffer.tell()
        self._buffer.seek(pos)
        return end - pos

    def read(self, size: int) -> bytes:
        data = self._buffer.read(size)
        if len(data) != size:
            raise EOFError(f"Unexpected EOF: requested {size}, got {len(data)} at offset {self.tell()}\")")
        return data

    def read_fmt(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        data = self.read(size)
        values = struct.unpack(fmt, data)
        return values[0] if len(values) == 1 else values

    def read_uint8(self) -> int: return self.read_fmt("<B")
    def read_int8(self) -> int: return self.read_fmt("<b")
    def read_bool(self) -> bool: return bool(self.read_uint8())
    def read_uint16(self) -> int: return self.read_fmt("<H")
    def read_int16(self) -> int: return self.read_fmt("<h")
    def read_uint32(self) -> int: return self.read_fmt("<I")
    def read_int32(self) -> int: return self.read_fmt("<i")
    def read_uint64(self) -> int: return self.read_fmt("<Q")
    def read_int64(self) -> int: return self.read_fmt("<q")
    def read_float32(self) -> float: return self.read_fmt("<f")
    def read_float64(self) -> float: return self.read_fmt("<d")
    def read_vector2(self) -> tuple[float, float]: return self.read_fmt("<2f")
    def read_vector3(self) -> tuple[float, float, float]: return self.read_fmt("<3f")
    def read_db_id(self) -> int: return self.read_int64()
    def read_object_id(self) -> int: return self.read_uint32()

    def read_u24(self) -> int:
        return struct.unpack('<I', self.read(3) + b'\x00')[0]

    def read_packed_int(self) -> int:
        length = self.read_uint8()
        if length == 0xFF:
            length = self.read_u24()
        return length

    def read_blob(self) -> bytes:
        length = self.read_packed_int()
        return self.read(length)

    def read_string(self) -> str:
        data = self.read_blob()
        return data.decode('utf-8', errors='replace') 

    def read_python(self) -> bytes:
        return self.read_blob()


class BinaryWriter:
    def __init__(self):
        self._buffer = bytearray()

    def get_bytes(self) -> bytes:
        return bytes(self._buffer)

    def tell(self) -> int:
        return len(self._buffer)

    def write_fmt(self, fmt: str, *values):
        data = struct.pack(fmt, *values)
        self._buffer.extend(data)

    def write_uint8(self, val: int): self.write_fmt("<B", val)
    def write_int8(self, val: int): self.write_fmt("<b", val)
    def write_bool(self, val: bool): self.write_uint8(1 if val else 0)
    def write_uint16(self, val: int): self.write_fmt("<H", val)
    def write_int16(self, val: int): self.write_fmt("<h", val)
    def write_uint32(self, val: int): self.write_fmt("<I", val)
    def write_int32(self, val: int): self.write_fmt("<i", val)
    def write_uint64(self, val: int): self.write_fmt("<Q", val)
    def write_int64(self, val: int): self.write_fmt("<q", val)
    def write_float32(self, val: float): self.write_fmt("<f", val)
    def write_float64(self, val: float): self.write_fmt("<d", val)
    def write_vector2(self, val: tuple[float, float]): self.write_fmt("<2f", *val)
    def write_vector3(self, val: tuple[float, float, float]): self.write_fmt("<3f", *val)
    def write_db_id(self, val: int): self.write_int64(val)
    def write_object_id(self, val: int): self.write_uint32(val)

    def write_u24(self, val: int):
        self._buffer.extend(struct.pack('<I', val & 0xFFFFFF)[:3])

    def write_packed_int(self, length: int):
        if length <= 254:
            self.write_uint8(length)
        else:
            self.write_uint8(0xFF)
            self.write_u24(length)

    def write_blob(self, data: bytes):
        self.write_packed_int(len(data))
        self._buffer.extend(data)

    def write_string(self, val: str):
        self.write_blob(val.encode('utf-8'))

    def write_python(self, val: Any) -> bytes:
        """Интеграция рекурсивной бескомпиляционной сериализации."""
        return pack_python_object_generic(val)


# ============================================================================
# 6. Основные функции интеграции (сериализация / десериализация)
# ============================================================================

def serialize_account(account_data: dict[str, Any]) -> bytes:
    import pickle
    import struct
    writer = BinaryWriter()
    
    # Динамически загружаем и фильтруем только CLIENT-visible свойства!
    client_properties = load_client_properties()
    sorted_props = get_sorted_properties(client_properties)

    print(f"[SERIALIZER] Запуск паковки свойств сущности Account. К отправке клиенту допущено: {len(sorted_props)} из {len(account_data)}")

    for prop in sorted_props:
        t = prop.type_name
        val = account_data.get(prop.name)
        
        # === ХАРДКОРНЫЙ ПЕРЕХВАТ ДЛЯ НАШЕЙ СТРУКТУРЫ ===
        if prop.name == 'initialServerSettings':
            # 1. Если пришли байты пикла — насильно распаковываем их обратно в dict
            if isinstance(val, bytes) and val.startswith(b'\x80\x02'):
                try:
                    val = pickle.loads(val)
                except Exception:
                    pass
            
            # 2. Задаем дефолты для нативной структуры C++
            isB2P = False
            starting_day = 0
            starting_time = 0
            roaming_id = 0
            roaming_center_ex = "RU"
            roaming_center_src = "RU"

            # 3. Вытаскиваем боевые параметры из словаря
            if isinstance(val, dict):
                isB2P = val.get('isB2P', False)
                reg_settings = val.get('regional_settings', {})
                if isinstance(reg_settings, dict):
                    starting_day = reg_settings.get('starting_day_of_a_new_day', reg_settings.get('starting_day', 0))
                    starting_time = reg_settings.get('starting_time_of_a_new_day', reg_settings.get('starting_time', 0))
                roaming = val.get('roaming', (0, 'RU', 'RU'))
                if isinstance(roaming, (tuple, list)) and len(roaming) >= 3:
                    roaming_id = roaming[0]
                    roaming_center_ex = roaming[1]
                    roaming_center_src = roaming[2]

            # 4. Упаковываем поля строго друг за другом (19 байт payload)
            payload = bytearray()
            payload.extend(struct.pack('<B', 1 if isB2P else 0))       # 1 байт
            payload.extend(struct.pack('<i', starting_day))           # 4 байта
            payload.extend(struct.pack('<i', starting_time))          # 4 байта
            payload.extend(struct.pack('<i', roaming_id))             # 4 байта
            
            r1 = roaming_center_ex.encode('utf-8') if isinstance(roaming_center_ex, str) else bytes(roaming_center_ex)
            payload.extend(struct.pack('<B', len(r1)) + r1)           # префикс длины + "RU"
            
            r2 = roaming_center_src.encode('utf-8') if isinstance(roaming_center_src, str) else bytes(roaming_center_src)
            payload.extend(struct.pack('<B', len(r2)) + r2)           # префикс длины + "RU"

            # 5. Собираем финальный блоб с префиксом длины всей структуры (0x13)
            data = bytes([len(payload)]) + bytes(payload)
            
            # 6. Фиксируем смещение, пишем прямо в поток и логируем
            start_offset = writer.tell()
            writer.write_blob(data)  # или writer.append(data) / writer.write(data) в зависимости от твоего BinaryWriter
            
            print(f"[SERIALIZER] Property: {prop.name:<25} | Type: {t:<10} | Offset: {start_offset:<4d} | Bytes: {data.hex(' ').upper()}")
            
            # СКИПАЕМ ОСТАЛЬНУЮ ЛОГИКУ ДЛЯ ЭТОГО СВОЙСТВА!
            continue 
        
        # === ОБЩАЯ ЛОГИКА ДЛЯ ВСЕХ ОСТАЛЬНЫХ СВОЙСТВ СУЩНОСТИ ===
        if val is None:
            val = get_default_value_generic(t)

        start_offset = writer.tell()

        # Пакуем фиксированные и строковые значения
        if t == "UINT8": data = pack_uint8(val)
        elif t == "INT8": data = pack_int8(val)
        elif t == "BOOL": data = pack_uint8(1 if val else 0)
        elif t == "UINT16": data = pack_uint16(val)
        elif t == "INT16": data = pack_int16(val)
        elif t == "UINT32": data = pack_uint32(val)
        elif t == "INT32": data = pack_int32(val)
        elif t == "OBJECT_ID": data = pack_uint32(val)
        elif t == "UINT64": data = pack_uint64(val)
        elif t == "INT64": data = pack_int64(val)
        elif t == "DB_ID": data = pack_int64(val)
        elif t == "FLOAT32": data = pack_float32(val)
        elif t == "FLOAT64": data = pack_float64(val)
        elif t == "VECTOR2": data = pack_vector2(val)
        elif t == "VECTOR3": data = pack_vector3(val)
        elif t == "STRING": data = pack_string(val, len_type="packed_int")
        
        # === НАДЁЖНАЯ БИНАРНАЯ ПАКОВКА ДЛЯ BLOB (БЕЗ УЩЕРБА ДЛЯ ВЫСОКИХ БАЙТ) ===
        elif t == "BLOB":
            blob_bytes = val.encode('utf-8') if isinstance(val, str) else val
            length = len(blob_bytes)
            
            # Генерируем нативный префикс длины BigWorld PackedInt
            if length < 254:
                data = bytes([length]) + blob_bytes
            elif length < 65535:
                data = bytes([0xFE]) + struct.pack("<H", length) + blob_bytes
            else:
                data = bytes([0xFF]) + struct.pack("<I", length) + blob_bytes
            
        # === КРИТИЧЕСКИЙ БИНАРНЫЙ ФИКС ДЛЯ PYTHON (БЕЗ UTF-8 ИСКАЖЕНИЙ) ===
        elif t == "PYTHON":
            # Если прилетел сырой словарь/объект — пакуем в честный pickle protocol=2
            if not isinstance(val, bytes):
                pickle_bytes = pickle.dumps(val, protocol=2)
            else:
                pickle_bytes = val
            
            length = len(pickle_bytes)
            
            # Пакуем СТРОГО как сырые байты с правильным префиксом длины BigWorld.
            # Никаких .decode('latin1'), никаких pack_string! Только чистый бинарный поток!
            if length < 254:
                data = bytes([length]) + pickle_bytes
            elif length < 65535:
                data = bytes([0xFE]) + struct.pack("<H", length) + pickle_bytes
            else:
                data = bytes([0xFF]) + struct.pack("<I", length) + pickle_bytes
        # ====================================================================
        
        else:
            raise NotImplementedError(f"Serialization for {t} not implemented!")

        writer._buffer.extend(data)
        
        # Нативное С++ Mercury логирование для дебага
        print(f"[SERIALIZER] Property: {prop.name:<25} | Type: {t:<10} | Offset: {start_offset:<4d} | Bytes: {data.hex(' ').upper()}")

    return writer.get_bytes()


def unpack_initial_server_settings(reader: BinaryReader) -> dict:
    """Десериализация INITIAL_SERVER_SETTINGS по правилам натического C++ FIXED_DICT."""
    isB2P = reader.read_bool()
    
    # premiumPlusBonus (FIXED_DICT_EMPTY) - 0 bytes
    
    # regional_settings (REGIONAL_SETTINGS)
    starting_day = reader.read_int32()
    starting_time = reader.read_int32()
    regional_settings = {
        "starting_day_of_a_new_day": starting_day,
        "starting_time_of_a_new_day": starting_time
    }
    
    # roaming (ROAMING_TUPLE)
    roaming_int = reader.read_int32()
    roaming_str1 = reader.read_string() 
    roaming_str2 = reader.read_string()
    roaming = (roaming_int, roaming_str1, roaming_str2)
    
    # wgcg (FIXED_DICT_EMPTY) - 0 bytes
    
    return {
        "isB2P": isB2P,
        "premiumPlusBonus": {},
        "regional_settings": regional_settings,
        "roaming": roaming,
        "wgcg": {},
    }


def deserialize_account(data: bytes) -> dict[str, Any]:
    reader = BinaryReader(data)
    
    client_properties = load_client_properties()
    sorted_props = get_sorted_properties(client_properties)
    result = {}

    for prop in sorted_props:
        t = prop.type_name
        
        if t == "UINT8": val = reader.read_uint8()
        elif t == "INT8": val = reader.read_int8()
        elif t == "BOOL": val = reader.read_bool()
        elif t == "UINT16": val = reader.read_uint16()
        elif t == "INT16": val = reader.read_int16()
        elif t == "UINT32": val = reader.read_uint32()
        elif t == "INT32": val = reader.read_int32()
        elif t == "OBJECT_ID": val = reader.read_object_id()
        elif t == "UINT64": val = reader.read_uint64()
        elif t == "INT64": val = reader.read_int64()
        elif t == "DB_ID": val = reader.read_db_id()
        elif t == "FLOAT32": val = reader.read_float32()
        elif t == "FLOAT64": val = reader.read_float64()
        elif t == "VECTOR2": val = reader.read_vector2()
        elif t == "VECTOR3": val = reader.read_vector3()
        elif t == "STRING": val = reader.read_string()
        elif t == "BLOB": val = reader.read_blob()
        elif t == "PYTHON":
            blob_data = reader.read_blob()
            if not blob_data:
                val = None
            else:
                if prop.name == "initialServerSettings":
                    sub_reader = BinaryReader(blob_data)
                    val = unpack_initial_server_settings(sub_reader)
                else:
                    val = blob_data
        else:
            raise NotImplementedError(f"Deserialization for {t} is not implemented!")
        result[prop.name] = val
    return result


# ============================================================================
# 7. Self-tests / Round-trip
# ============================================================================

if __name__ == "__main__":
    # --- Тест A: граничные случаи packed_u24 ---
    print("=== Тест A: packed_u24 граничные длины ===")
    for n in (0, 1, 127, 253, 254, 255, 256, 4096, 0xFFFF, 0x123456):
        w = BinaryWriter(); w.write_packed_int(n)
        r = BinaryReader(w.get_bytes()); got = r.read_packed_int()
        ok = got == n
        expect_len = 1 if n <= 254 else 4
        print(f"  n={n:<10} bytes={len(w.get_bytes())} expect={expect_len} "
              f"roundtrip={'OK' if ok else 'FAIL'}")
        assert ok, f"packed_u24 round-trip FAIL for n={n}"
        assert len(w.get_bytes()) == expect_len, f"packed_u24 wire length FAIL n={n}"
    print("  [OK] все граничные длины проходят\n")

    # --- Тест B: полный round-trip всех 3 клиентских свойств Account (БЕЗ pickle) ---
    print("=== Test B: serialize -> deserialize Account ===")
    account = {
        "name": "OfflinePlayer",
        "incarnationID": 0,
        "initialServerSettings": {  
            "isB2P": False,
            "roaming": (0, "RU", "RU"),
            "wgcg": {},
            "premiumPlusBonus": {},
            "regional_settings": {
                "starting_day_of_a_new_day": 0,
                "starting_time_of_a_new_day": 0
            },
        },
    }

    blob = serialize_account(account)
    back = deserialize_account(blob)

    mismatches = []
    for prop in load_client_properties():
        a, b = account.get(prop.name), back.get(prop.name)
        ok = a == b
        if not ok:
            mismatches.append(f"{prop.name}: {a!r} != {b!r}")

    print(f"  serialized {len(blob)} байт, {len(load_client_properties())} свойств")
    if mismatches:
        print("  [FAIL] расхождения:")
        for m in mismatches:
            print(f"    {m}")
        raise SystemExit(1)
    print("  [OK] все свойства round-trip совпали (нативная DataStream сериализация!)")
