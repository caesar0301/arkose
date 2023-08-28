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
"""
Constants required for dbt 
"""

from enum import Enum

# Based on https://schemas.getdbt.com/dbt/manifest/v7/index.html
REQUIRED_MANIFEST_KEYS = ["name", "schema", "resource_type"]

# Based on https://schemas.getdbt.com/dbt/catalog/v1.json
REQUIRED_CATALOG_KEYS = ["name", "type", "index"]

NONE_KEYWORDS_LIST = ["none", "null"]

DBT_CATALOG_FILE_NAME = "catalog.json"
DBT_MANIFEST_FILE_NAME = "manifest.json"
DBT_RUN_RESULTS_FILE_NAME = "run_results.json"

DBT_FILE_NAMES_LIST = [
    DBT_CATALOG_FILE_NAME,
    DBT_MANIFEST_FILE_NAME,
    DBT_RUN_RESULTS_FILE_NAME,
]


class SkipResourceTypeEnum(Enum):
    """
    Enum for nodes to be skipped
    """

    ANALYSIS = "analysis"
    TEST = "test"


class CompiledQueriesEnum(Enum):
    """
    Enum for Compiled Queries
    """

    COMPILED_CODE = "compiled_code"
    COMPILED_SQL = "compiled_sql"


class RawQueriesEnum(Enum):
    """
    Enum for Raw Queries
    """

    RAW_CODE = "raw_code"
    RAW_SQL = "raw_sql"


class DbtTestSuccessEnum(Enum):
    """
    Enum for success messages of dbt tests
    """

    SUCCESS = "success"
    PASS = "pass"


class DbtTestFailureEnum(Enum):
    """
    Enum for failure message of dbt tests
    """

    FAILURE = "failure"
    FAIL = "fail"


class DbtCommonEnum(Enum):
    """
    Common enum for dbt
    """

    OWNER = "owner"
    NODES = "nodes"
    SOURCES = "sources"
    RESOURCETYPE = "resource_type"
    MANIFEST_NODE = "manifest_node"
    UPSTREAM = "upstream"
    RESULTS = "results"
    TEST_SUITE_NAME = "test_suite_name"
    DBT_TEST_SUITE = "DBT_TEST_SUITE"
