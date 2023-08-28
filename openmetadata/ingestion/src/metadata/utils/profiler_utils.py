#  Copyright 2021 Collate
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Profiler utils class and functions"""

import re
from collections import defaultdict
from datetime import datetime
from functools import reduce
from typing import Optional, Tuple

import sqlparse
from pydantic import BaseModel

from metadata.utils.logger import profiler_logger

logger = profiler_logger()

PARSING_TIMEOUT = 10


class QueryResult(BaseModel):
    """System metric query result shared by Redshift and Snowflake"""

    database_name: str
    schema_name: str
    table_name: str
    query_type: str
    timestamp: datetime
    query_id: Optional[str] = None
    query_text: Optional[str] = None
    rows: Optional[int] = None


def clean_up_query(query: str) -> str:
    """remove comments and newlines from query"""
    return sqlparse.format(query, strip_comments=True).replace("\\n", "")


def get_identifiers_from_string(
    identifier: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """given a string identifier try to fetch the database, schema and table names.
    part of the identifier name as `"DATABASE.DOT"` will be returned on the left side of the tuple
    and the rest of the identifier name as `"SCHEMA.DOT.TABLE"` will be returned on the right side of the tuple

    Args:
        identifier (str): table identifier

    Returns:
        Tuple[str, str, str]: database, schema and table names
    """
    pattern = r"\"([^\"]+)\"|(\w+(?:\.\w+)*(?:\.\w+)*)"
    matches = re.findall(pattern, identifier)

    values = []
    for match in matches:
        if match[0] != "":
            values.append(match[0])
        if match[1] != "":
            split_match = match[1].split(".")
            values.extend(split_match)

    database_name, schema_name, table_name = ([None] * (3 - len(values))) + values
    return database_name, schema_name, table_name


def get_value_from_cache(cache: dict, key: str):
    """given a dict of cache and a key, return the value if exists

    Args:
        cache (dict): dict of cache
        key (str): key to look for in the cache
    """
    try:
        return reduce(dict.get, key.split("."), cache)
    except TypeError:
        return None


def set_cache(cache: defaultdict, key: str, value):
    """given a dict of cache, a key and a value, set the value in the cache

    Args:
        cache (dict): dict of cache
        key (str): key to set for in the cache
        value: value to set in the cache
    """
    split_key = key.split(".")
    for indx, key_ in enumerate(split_key):
        if indx == len(split_key) - 1:
            cache[key_] = value
            break
        cache = cache[key_]
