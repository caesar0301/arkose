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
Min Metric definition
"""
# pylint: disable=duplicate-code

from sqlalchemy import TIME, column
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import GenericFunction

from metadata.profiler.metrics.core import CACHE, StaticMetric, _label
from metadata.profiler.orm.functions.length import LenFn
from metadata.profiler.orm.registry import (
    Dialects,
    is_concatenable,
    is_date_time,
    is_quantifiable,
)


class MinFn(GenericFunction):
    name = __qualname__
    inherit_cache = CACHE


@compiles(MinFn)
def _(element, compiler, **kw):
    col = compiler.process(element.clauses, **kw)
    return f"MIN({col})"


@compiles(MinFn, Dialects.MySQL)
@compiles(MinFn, Dialects.MariaDB)
def _(element, compiler, **kw):
    col = compiler.process(element.clauses, **kw)
    col_type = element.clauses.clauses[0].type
    if isinstance(col_type, TIME):
        # Mysql Sqlalchemy returns timedelta which is not supported pydantic type
        # hence we profile the time by modifying it in seconds
        return f"MIN(TIME_TO_SEC({col}))"
    return f"MIN({col})"


class Min(StaticMetric):
    """
    MIN Metric

    Given a column, return the min value.
    """

    @classmethod
    def name(cls):
        return "min"

    @_label
    def fn(self):
        """sqlalchemy function"""
        if is_concatenable(self.col.type):
            return MinFn(LenFn(column(self.col.name, self.col.type)))

        if (not is_quantifiable(self.col.type)) and (not is_date_time(self.col.type)):
            return None
        return MinFn(column(self.col.name, self.col.type))

    def df_fn(self, dfs=None):
        """pandas function"""
        if is_quantifiable(self.col.type) or is_date_time(self.col.type):
            return min((df[self.col.name].min() for df in dfs))
        return 0
