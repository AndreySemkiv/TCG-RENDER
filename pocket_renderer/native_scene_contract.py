"""Validate only the native scene-controller behavior a selected card uses."""
from functools import lru_cache
from pathlib import Path
import json
import struct

from .config import CODE_ROOT
from .native_text_contract import source


NAMESPACE = 'Lettuce.Infrastructure.Card.Core'


def method_contract(native, type_name, method_name, namespace=NAMESPACE):
    address = native.method(namespace, type_name, method_name)
    literals, constants = [], []
    for reference in native.method_references(namespace, type_name, method_name):
        relative = reference['pc'] - address
        if reference['kind'] == 'load' and reference['word'] >> 24 in (0xfd, 0xbd, 0x3d):
            width = reference['width']
            segment = next((segment for segment in native.segments
                            if segment[0] == 1 and segment[3] <= reference['target']
                            and reference['target']+width <= segment[3]+segment[6]), None)
            if segment is not None and segment[1] & 2:
                # Static mutable state (including zero-filled BSS) is not a
                # read-only numeric constant and is not interpreted as one.
                continue
            offset = native.offset(reference['target'], width)
            constants.append({'offset': relative, 'bytes': native.binary[offset:offset+width].hex()})
        elif reference['kind'] in ('load', 'address'):
            try:
                value = native.literal(reference['target'])
            except (ValueError, UnicodeError):
                continue
            literals.append({'offset': relative, 'value': value})
    return {'shape': native.normalized_method(namespace, type_name, method_name),
            'literals': literals, 'constants': constants}


def field_contract(native, namespace, type_name):
    _, row = native.type_row(namespace, type_name)
    start, _ = native.sections[11]
    return [native.text(struct.unpack_from('<i', native.metadata, start+i*12)[0])
            for i in range(row[8], row[8]+row[18])]


@lru_cache(maxsize=32)
def validate_scene_contract(root, family):
    native = source(str(Path(root).resolve()))
    contract = json.loads((CODE_ROOT/'profiles/native-scene-contract-v1.json').read_text(encoding='utf-8'))
    if family not in contract['families']:
        raise ValueError('Original scene controller is not supported: ' + family)
    classes = [family] + contract['common'] + contract.get('dependencies', {}).get(family, [])
    for type_name in classes:
        expected = contract['families'][type_name]
        namespace = expected['namespace']
        if field_contract(native, namespace, type_name) != expected['fields']:
            raise ValueError('Original native scene controller fields changed: ' + type_name)
        for method_name, variants in expected['methods'].items():
            if method_contract(native, type_name, method_name, namespace) not in variants:
                raise ValueError('Original native scene controller changed: ' + type_name + '.' + method_name)
    return dict(native.provenance, scene_family=family, contract=contract['id'])
