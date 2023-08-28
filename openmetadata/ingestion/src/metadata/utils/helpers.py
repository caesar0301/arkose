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
Helpers module for ingestion related methods
"""

from __future__ import annotations

import itertools
import re
import shutil
import sys
from datetime import datetime, timedelta
from functools import wraps
from math import floor, log
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import sqlparse
from sqlparse.sql import Statement

from metadata.generated.schema.entity.data.chart import ChartType
from metadata.generated.schema.entity.data.table import Column, Table
from metadata.generated.schema.entity.services.databaseService import DatabaseService
from metadata.generated.schema.type.tagLabel import TagLabel
from metadata.utils.constants import DEFAULT_DATABASE
from metadata.utils.logger import utils_logger

logger = utils_logger()


class BackupRestoreArgs:
    def __init__(  # pylint: disable=too-many-arguments
        self,
        host: str,
        user: str,
        password: str,
        database: str,
        port: str,
        options: List[str],
        arguments: List[str],
        schema: Optional[str] = None,
    ):
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        self.options = options
        self.arguments = arguments
        self.schema = schema


class DockerActions:
    def __init__(
        self,
        start: bool,
        stop: bool,
        pause: bool,
        resume: bool,
        clean: bool,
        reset_db: bool,
    ):
        self.start = start
        self.stop = stop
        self.pause = pause
        self.resume = resume
        self.clean = clean
        self.reset_db = reset_db


om_chart_type_dict = {
    "line": ChartType.Line,
    "big_number": ChartType.Line,
    "big_number_total": ChartType.Line,
    "dual_line": ChartType.Line,
    "line_multi": ChartType.Line,
    "table": ChartType.Table,
    "dist_bar": ChartType.Bar,
    "bar": ChartType.Bar,
    "box_plot": ChartType.BoxPlot,
    "boxplot": ChartType.BoxPlot,
    "histogram": ChartType.Histogram,
    "treemap": ChartType.Area,
    "area": ChartType.Area,
    "pie": ChartType.Pie,
    "text": ChartType.Text,
    "scatter": ChartType.Scatter,
}


def calculate_execution_time(func):
    """
    Method to calculate workflow execution time
    """

    @wraps(func)
    def calculate_debug_time(*args, **kwargs):
        start = perf_counter()
        func(*args, **kwargs)
        end = perf_counter()
        logger.debug(
            f"{func.__name__} executed in { pretty_print_time_duration(end - start)}"
        )

    return calculate_debug_time


def calculate_execution_time_generator(func):
    """
    Generator method to calculate workflow execution time
    """

    def calculate_debug_time(*args, **kwargs):
        start = perf_counter()
        yield from func(*args, **kwargs)
        end = perf_counter()
        logger.debug(
            f"{func.__name__} executed in { pretty_print_time_duration(end - start)}"
        )

    return calculate_debug_time


def pretty_print_time_duration(duration: Union[int, float]) -> str:
    """
    Method to format and display the time
    """

    days = divmod(duration, 86400)[0]
    hours = divmod(duration, 3600)[0]
    minutes = divmod(duration, 60)[0]
    seconds = round(divmod(duration, 60)[1], 2)
    if days:
        return f"{days}day(s) {hours}h {minutes}m {seconds}s"
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def get_start_and_end(duration: int = 0):
    """
    Method to return start and end time based on duration
    """

    today = datetime.utcnow()
    start = (today + timedelta(0 - duration)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Add one day to make sure we are handling today's queries
    end = (today + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def snake_to_camel(snake_str):
    """
    Method to convert snake case text to camel case
    """
    split_str = snake_str.split("_")
    split_str[0] = split_str[0].capitalize()
    if len(split_str) > 1:
        split_str[1:] = [u.title() for u in split_str[1:]]
    return "".join(split_str)


def datetime_to_ts(date: Optional[datetime]) -> Optional[int]:
    """
    Convert a given date to a timestamp as an Int in milliseconds
    """
    return int(date.timestamp() * 1_000) if date else None


def get_formatted_entity_name(name: str) -> Optional[str]:
    """
    Method to get formatted entity name
    """

    return (
        name.replace("[", "").replace("]", "").replace("<default>.", "")
        if name
        else None
    )


def replace_special_with(raw: str, replacement: str) -> str:
    """
    Replace special characters in a string by a hyphen
    :param raw: raw string to clean
    :param replacement: string used to replace
    :return: clean string
    """
    return re.sub(r"[^a-zA-Z0-9]", replacement, raw)


def get_standard_chart_type(raw_chart_type: str) -> ChartType.Other:
    """
    Get standard chart type supported by OpenMetadata based on raw chart type input
    :param raw_chart_type: raw chart type to be standardize
    :return: standard chart type
    """
    if raw_chart_type is not None:
        return om_chart_type_dict.get(raw_chart_type.lower(), ChartType.Other)
    return ChartType.Other


def find_in_iter(element: Any, container: Iterable[Any]) -> Optional[Any]:
    """
    If the element is in the container, return it.
    Otherwise, return None
    :param element: to find
    :param container: container with element
    :return: element or None
    """
    return next((elem for elem in container if elem == element), None)


def find_column_in_table(column_name: str, table: Table) -> Optional[Column]:
    """
    If the column exists in the table, return it
    """
    return next(
        (col for col in table.columns if col.name.__root__ == column_name), None
    )


def find_column_in_table_with_index(
    column_name: str, table: Table
) -> Optional[Tuple[int, Column]]:
    """Return a column and its index in a Table Entity

    Args:
         column_name (str): column to find
         table (Table): Table Entity

    Return:
          A tuple of Index, Column if the column is found
    """
    col_index, col = next(
        (
            (col_index, col)
            for col_index, col in enumerate(table.columns)
            if str(col.name.__root__).lower() == column_name.lower()
        ),
        (None, None),
    )

    return col_index, col


def list_to_dict(original: Optional[List[str]], sep: str = "=") -> Dict[str, str]:
    """
    Given a list with strings that have a separator,
    convert that to a dictionary of key-value pairs
    """
    if not original:
        return {}

    split_original = [
        (elem.split(sep)[0], elem.split(sep)[1]) for elem in original if sep in elem
    ]
    return dict(split_original)


def clean_up_starting_ending_double_quotes_in_string(string: str) -> str:
    """Remove start and ending double quotes in a string

    Args:
        string (str): a string

    Raises:
        TypeError: An error occure checking the type of `string`

    Returns:
        str: a string with no double quotes
    """
    if not isinstance(string, str):
        raise TypeError(f"{string}, must be of type str, instead got `{type(string)}`")

    return string.strip('"')


def insensitive_replace(raw_str: str, to_replace: str, replace_by: str) -> str:
    """Replace `to_replace` by `replace_by` in `raw_str` ignoring the raw_str case.

    Args:
        raw_str:str: Define the string that will be searched
        to_replace:str: Specify the string to be replaced
        replace_by:str: Replace the to_replace:str parameter in the raw_str:str string

    Returns:
        A string where the given to_replace is replaced by replace_by in raw_str, ignoring case
    """

    return re.sub(to_replace, replace_by, raw_str, flags=re.IGNORECASE | re.DOTALL)


def insensitive_match(raw_str: str, to_match: str) -> bool:
    """Match `to_match` in `raw_str` ignoring the raw_str case.

    Args:
        raw_str:str: Define the string that will be searched
        to_match:str: Specify the string to be matched

    Returns:
        True if `to_match` matches in `raw_str`, ignoring case. Otherwise, false.
    """

    return re.match(to_match, raw_str, flags=re.IGNORECASE | re.DOTALL) is not None


def get_entity_tier_from_tags(tags: list[TagLabel]) -> Optional[str]:
    """_summary_

    Args:
        tags (list[TagLabel]): list of tags

    Returns:
        Optional[str]
    """
    if not tags:
        return None
    return next(
        (
            tag.tagFQN.__root__
            for tag in tags
            if tag.tagFQN.__root__.lower().startswith("tier")
        ),
        None,
    )


def format_large_string_numbers(number: Union[float, int]) -> str:
    """Format large string number to a human readable format.
    (e.g. 1,000,000 -> 1M, 1,000,000,000 -> 1B, etc)

    Args:
        number: number
    """
    if number == 0:
        return "0"
    units = ["", "K", "M", "B", "T"]
    constant_k = 1000.0
    magnitude = int(floor(log(abs(number), constant_k)))
    return f"{number / constant_k**magnitude:.2f}{units[magnitude]}"


def clean_uri(uri: str) -> str:
    """
    if uri is like http://localhost:9000/
    then remove the end / and
    make it http://localhost:9000
    """
    return uri[:-1] if uri.endswith("/") else uri


def deep_size_of_dict(obj: dict) -> int:
    """Get deepsize of dict data structure

    Args:
        obj (dict): dict data structure
    Returns:
        int: size of dict data structure
    """
    # pylint: disable=unnecessary-lambda-assignment
    dict_handler = lambda elmt: itertools.chain.from_iterable(elmt.items())
    handlers = {
        dict: dict_handler,
        list: iter,
    }

    seen = set()

    def sizeof(obj) -> int:
        if id(obj) in seen:
            return 0

        seen.add(id(obj))
        size = sys.getsizeof(obj, 0)
        for type_, handler in handlers.items():
            if isinstance(obj, type_):
                size += sum(map(sizeof, handler(obj)))
                break

        return size

    return sizeof(obj)


def is_safe_sql_query(sql_query: str) -> bool:
    """Validate SQL query
    Args:
        sql_query (str): SQL query
    Returns:
        bool
    """

    forbiden_token = {
        "CREATE",
        "ALTER",
        "DROP",
        "TRUNCATE",
        "COMMENT",
        "RENAME",
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "CALL",
        "EXPLAIN PLAN",
        "LOCK TABLE",
        "UNLOCK TABLE",
        "GRANT",
        "REVOKE",
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT",
        "SET TRANSACTION",
    }

    parsed_queries: Tuple[Statement] = sqlparse.parse(sql_query)
    for parsed_query in parsed_queries:
        validation = [
            token.normalized in forbiden_token for token in parsed_query.tokens
        ]
        if any(validation):
            return False
    return True


def get_database_name_for_lineage(
    db_service_entity: DatabaseService, default_db_name: Optional[str]
) -> Optional[str]:
    # If the database service supports multiple db or
    # database service connection details are not available
    # then pick the database name available from api response
    if db_service_entity.connection is None or hasattr(
        db_service_entity.connection.config, "supportsDatabase"
    ):
        return default_db_name

    # otherwise if it is an single db source then use "databaseName"
    # and if databaseName field is not available or is empty then use
    # "default" as database name
    return (
        db_service_entity.connection.config.__dict__.get("databaseName")
        or DEFAULT_DATABASE
    )


def delete_dir_content(directory: str) -> None:
    location = Path(directory)
    if location.is_dir():
        logger.info("Location exists, cleaning it up")
        shutil.rmtree(directory)


def init_staging_dir(directory: str) -> None:
    """
    Prepare the the staging directory
    """
    delete_dir_content(directory=directory)
    location = Path(directory)
    logger.info(f"Creating the directory to store staging data in {location}")
    location.mkdir(parents=True, exist_ok=True)
