#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entity System Addon для WoT Offline Emulator
Интегрируется с server_stub.py без изменений основного кода.
Использование:
    from entity_addon import EntitySystemAddon
    addon = EntitySystemAddon()
    addon.inject_into_baseapp(baseapp_stub_instance)
"""
import struct
from typing import Dict, Any, Optional
from pathlib import Path
# ───────────────────────────────────────────────────────────────
# Парсер .def файлов (упрощённая версия из server/entity_defs_parser.py)
# ───────────────────────────────────────────────────────────────
class EntityDefParser:
    """Парсер .def файлов BigWorld"""
    
    TYPE_MAP = {
        'INT8': 'int', 'INT16': 'int', 'INT32': 'int', 'INT64': 'int',
        'UINT8': 'int', 'UINT16': 'int', 'UINT32': 'int', 'UINT64': 'int',
        'FLOAT32': 'float', 'FLOAT64': 'float', 'FLOAT': 'float',
        'BOOL': 'bool', 'STRING': 'str',
        'VECTOR2': 'Vector2', 'VECTOR3': 'Vector3', 'VECTOR4': 'Vector4',
        'OBJECT_ID': 'int', 'DB_ID': 'int', 'PYTHON': 'Any',
    }
    
    def __init__(self):
        self.entities: Dict[str, Dict] = {}
    
    def parse_file(self, file_path: str) -> Optional[Dict]:
        """Парсинг .def файла"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f'[ENTITY] Ошибка чтения {file_path}: {e}')
            return None
        
        import re
        
        class_name = Path(file_path).stem
        entity = {
            'name': class_name,
            'interfaces': [],
            'properties': [],
            'client_methods': [],
        }
        
        # Interfaces
        impl_match = re.search(r'<Implements>(.*?)</Implements>', content, re.DOTALL)
        if impl_match:
            entity['interfaces'] = re.findall(r'<Interface>([^<]+)</Interface>', impl_match.group(1))
        
        # Properties
        props_match = re.search(r'<Properties>(.*?)</Properties>', content, re.DOTALL)
        if props_match:
            for prop_match in re.finditer(r'<(\w+)>(.*?)</\1>', props_match.group(1), re.DOTALL):
                prop_name = prop_match.group(1)
                prop_body = prop_match.group(2)
                
                if prop_name in ['Type', 'Flags', 'Default', 'DetailLevel']:
                    continue
                
                type_match = re.search(r'<Type>(.*?)</Type>', prop_body, re.DOTALL)
                flags_match = re.search(r'<Flags>([^<]+)</Flags>', prop_body)
                
                if type_match:
                    prop_type = self.TYPE_MAP.get(type_match.group(1).strip().split()[0], 'Any')
                    entity['properties'].append({
                        'name': prop_name,
                        'type': prop_type,
                        'flags': flags_match.group(1).strip() if flags_match else 'BASE',
                    })
        
        # ClientMethods
        cm_match = re.search(r'<ClientMethods>(.*?)</ClientMethods>', content, re.DOTALL)
        if cm_match:
            entity['client_methods'] = re.findall(r'<(\w+)>', cm_match.group(1))
        
        self.entities[class_name] = entity
        return entity
    
    def get_entity(self, name: str) -> Optional[Dict]:
        return self.entities.get(name)
# ───────────────────────────────────────────────────────────────
# Entity System Addon
# ───────────────────────────────────────────────────────────────
class EntitySystemAddon:
    """
    Добавляет Entity System функционал в BaseApp.
    
    После инициализации:
    - Парсит Account.def, Avatar.def, Vehicle.def
    - Добавляет метод generate_account_stream()
    - Добавляет метод generate_vehicle_stream()
    """
    
    def __init__(self, defs_path: str = '../for-ai'):
        self.parser = EntityDefParser()
        self.defs_path = Path(defs_path)
        self.initialized = False
        self.entities_created: Dict[int, Dict[str, Any]] = {}
        self.next_entity_id = 1
        
        print('[ENTITY] EntitySystemAddon создан')
    
    def initialize(self):
        """Инициализация — парсинг .def файлов"""
        if self.initialized:
            return
        
        # Парсим ключевые файлы
        key_files = [
            'entity_defs/Account.def',
            'entity_defs/Avatar.def',
            'entity_defs/Vehicle.def',
            'component_defs/HealthComponent.def',
            'component_defs/DeathComponent.def',
        ]
        
        for file_rel in key_files:
            file_path = self.defs_path / file_rel
            if file_path.exists():
                entity = self.parser.parse_file(str(file_path))
                if entity:
                    print(f'[ENTITY] ✅ Загружен: {file_rel} ({len(entity["properties"])} свойств)')
            else:
                print(f'[ENTITY] ⚠️  Не найден: {file_rel}')
        
        self.initialized = True
    
    def create_account_entity(self, login: str, session_key: int) -> Dict[str, Any]:
        """Создание Account entity"""
        entity_id = self.next_entity_id
        self.next_entity_id += 1
        
        entity = {
            'id': entity_id,
            'class': 'Account',
            'login': login,
            'session_key': session_key,
            'properties': {
                'name': login.split('@')[0] if '@' in login else login,
                'dbid': entity_id,
                'state': 1,  # LOGGED_ON
            }
        }
        
        self.entities_created[entity_id] = entity
        print(f'[ENTITY] Account создан: ID={entity_id}, login={login}')
        return entity
    
    def create_vehicle_entity(self, vehicle_type_id: int, owner_dbid: int,
                               position: tuple = (0, 0, 0)) -> Dict[str, Any]:
        """Создание Vehicle entity"""
        entity_id = self.next_entity_id
        self.next_entity_id += 1
        
        entity = {
            'id': entity_id,
            'class': 'Vehicle',
            'vehicle_type_id': vehicle_type_id,
            'owner_dbid': owner_dbid,
            'properties': {
                'position': position,
                'yaw': 0.0,
                'health': 100,
                'is_alive': True,
            }
        }
        
        self.entities_created[entity_id] = entity
        print(f'[ENTITY] Vehicle создан: ID={entity_id}, type={vehicle_type_id}')
        return entity
    
    def generate_account_stream(self, entity_id: int) -> bytes:
        """
        Генерация stream данных Account entity для клиента.
        Формат: createEntityFromStream
        """
        if entity_id not in self.entities_created:
            return b''
        
        entity = self.entities_created[entity_id]
        account_def = self.parser.get_entity('Account')
        
        stream = bytearray()
        
        # Entity ID
        stream.extend(struct.pack('<I', entity_id))
        
        # Class name
        class_name = entity['class'].encode('utf-8')
        stream.extend(struct.pack('<B', len(class_name)))
        stream.extend(class_name)
        
        # Properties
        props = entity['properties']
        stream.extend(struct.pack('<H', len(props)))
        
        for prop_name, prop_value in props.items():
            # Name
            name_bytes = prop_name.encode('utf-8')
            stream.extend(struct.pack('<B', len(name_bytes)))
            stream.extend(name_bytes)
            
            # Type + Value
            if isinstance(prop_value, int):
                if prop_value < 0 or prop_value < 2**31:
                    stream.extend(b'\x03')  # INT32
                    stream.extend(struct.pack('<i', prop_value))
                else:
                    stream.extend(b'\x05')  # UINT64
                    stream.extend(struct.pack('<Q', prop_value))
            elif isinstance(prop_value, str):
                stream.extend(b'\x02')  # STRING
                value_bytes = prop_value.encode('utf-8')
                stream.extend(struct.pack('<H', len(value_bytes)))
                stream.extend(value_bytes)
        
        print(f'[ENTITY] Account stream: {len(stream)} байт, {len(props)} свойств')
        return bytes(stream)
    
    def get_account_properties_for_baseapp(self, entity_id: int) -> Dict[str, Any]:
        """Получение BASE-свойств Account для отправки в BaseApp reply"""
        if entity_id not in self.entities_created:
            return {}
        
        entity = self.entities_created[entity_id]
        account_def = self.parser.get_entity('Account')
        
        if not account_def:
            return entity['properties']
        
        # Фильтруем только BASE свойства
        base_props = {}
        for prop in account_def['properties']:
            if 'BASE' in prop['flags']:
                prop_name = prop['name']
                if prop_name in entity['properties']:
                    base_props[prop_name] = entity['properties'][prop_name]
        
        return base_props
    
    def inject_into_baseapp(self, baseapp_stub):
        """
        Внедрение Entity System в BaseApp stub.
        Добавляет методы:
        - baseapp_stub.entity_system = self
        - baseapp_stub.create_account_entity()
        - baseapp_stub.get_account_stream()
        """
        baseapp_stub.entity_system = self
        baseapp_stub.create_account_entity = self.create_account_entity
        baseapp_stub.generate_account_stream = self.generate_account_stream
        
        print('[ENTITY] ✅ Entity System внедрён в BaseApp')
# ───────────────────────────────────────────────────────────────
# Точка входа для тестирования
# ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('Testing EntitySystemAddon...')
    addon = EntitySystemAddon()
    addon.initialize()
    
    # Тест создания Account
    account = addon.create_account_entity('test_player', 12345)
    stream = addon.generate_account_stream(account['id'])
    print(f'Account stream: {stream.hex()}')
    
    # Тест создания Vehicle
    vehicle = addon.create_vehicle_entity(1, account['id'], (100, 0, 100))
    print(f'Vehicle created: {vehicle}')
