from typing import Optional

from diskcache import Cache
from google.protobuf.struct_pb2 import Struct
from joblib.hashing import NumpyHasher

from flytekit.models.literals import Literal, LiteralCollection, LiteralMap

# Location on the filesystem where serialized objects will be stored
# TODO: read from config
CACHE_LOCATION = "~/.flyte/local-cache"


def _recursive_hash_placement(literal: Literal) -> Literal:
    if literal.collection is not None:
        literals = [_recursive_hash_placement(literal) for literal in literal.collection.literals]
        return Literal(collection=LiteralCollection(literals=literals))
    elif literal.map is not None:
        literal_map = {}
        for key, literal in literal.map.literals.items():
            literal_map[key] = _recursive_hash_placement(literal)
        return Literal(map=LiteralMap(literal_map))

    # Base case
    if literal.hash is not None:
        return Literal(hash=literal.hash)
    else:
        return literal


class ProtoJoblibHasher(NumpyHasher):
    def save(self, obj):
        if isinstance(obj, Struct):
            obj = dict(
                rewrite_rule="google.protobuf.struct_pb2.Struct",
                cls=obj.__class__,
                obj=dict(sorted(obj.fields.items())),
            )
        NumpyHasher.save(self, obj)


def _calculate_cache_key(task_name: str, cache_version: str, input_literal_map: LiteralMap) -> str:
    # Traverse the literals and replace the literal with a new literal that only contains the hash
    literal_map_overridden = {}
    for key, literal in input_literal_map.literals.items():
        literal_map_overridden[key] = _recursive_hash_placement(literal)

    # Generate a hash key of inputs with joblib
    hashed_inputs = ProtoJoblibHasher().hash(literal_map_overridden)
    return f"{task_name}-{cache_version}-{hashed_inputs}"


class LocalTaskCache(object):
    """
    This class implements a persistent store able to cache the result of local task executions.
    """

    _cache: Cache
    _initialized: bool = False

    @staticmethod
    def initialize():
        LocalTaskCache._cache = Cache(CACHE_LOCATION)
        LocalTaskCache._initialized = True

    @staticmethod
    def clear():
        if not LocalTaskCache._initialized:
            LocalTaskCache.initialize()
        LocalTaskCache._cache.clear()

    @staticmethod
    def get(task_name: str, cache_version: str, input_literal_map: LiteralMap) -> Optional[LiteralMap]:
        if not LocalTaskCache._initialized:
            LocalTaskCache.initialize()
        return LocalTaskCache._cache.get(_calculate_cache_key(task_name, cache_version, input_literal_map))

    @staticmethod
    def set(task_name: str, cache_version: str, input_literal_map: LiteralMap, value: LiteralMap) -> None:
        if not LocalTaskCache._initialized:
            LocalTaskCache.initialize()
        LocalTaskCache._cache.add(_calculate_cache_key(task_name, cache_version, input_literal_map), value)
