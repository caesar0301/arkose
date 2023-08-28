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
Status output utilities
"""
import json
import pprint
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class StackTraceError(BaseModel):
    """
    Class that represents a failure status
    """

    name: str
    error: str
    stack_trace: Optional[str]


class Status(BaseModel):
    """
    Class to handle status
    """

    records: List[Any] = Field(default_factory=list)
    warnings: List[Any] = Field(default_factory=list)
    failures: List[StackTraceError] = Field(default_factory=list)

    def as_obj(self) -> dict:
        return self.__dict__

    def as_string(self) -> str:
        return pprint.pformat(self.as_obj(), width=150)

    def as_json(self) -> str:
        return json.dumps(self.as_obj())

    def failed(self, name: str, error: str, stack_trace: Optional[str] = None) -> None:
        """
        Add a failure to the list of failures
        Args:
            name: the entity or record name
            error: the error with the exception
            stack_trace: the return of calling to traceback.format_exc()
        """
        self.failures.append(
            StackTraceError(name=name, error=error, stack_trace=stack_trace)
        )

    def fail_all(self, failures: List[StackTraceError]) -> None:
        """
        Add a list of failures
        Args:
            failures: a list of stack tracer errors
        """
        self.failures.extend(failures)
