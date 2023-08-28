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
Interfaces with database for all database engine
supporting sqlalchemy abstraction layer
"""
from datetime import datetime, timezone
from typing import Optional

from metadata.data_quality.interface.test_suite_interface import TestSuiteInterface
from metadata.data_quality.validations.validator import Validator
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.entity.services.connections.database.datalakeConnection import (
    DatalakeConnection,
)
from metadata.generated.schema.tests.basic import TestCaseResult
from metadata.generated.schema.tests.testCase import TestCase
from metadata.generated.schema.tests.testDefinition import TestDefinition
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.connections import get_connection
from metadata.mixins.pandas.pandas_mixin import PandasInterfaceMixin
from metadata.utils.importer import import_test_case_class
from metadata.utils.logger import test_suite_logger

logger = test_suite_logger()


class PandasTestSuiteInterface(TestSuiteInterface, PandasInterfaceMixin):
    """
    Sequential interface protocol for testSuite and Profiler. This class
    implements specific operations needed to run profiler and test suite workflow
    against a Datalake source.
    """

    def __init__(
        self,
        service_connection_config: DatalakeConnection,
        ometa_client: OpenMetadata,
        table_entity: Table = None,
    ):
        self.table_entity = table_entity

        self.ometa_client = ometa_client
        self.service_connection_config = service_connection_config

        (
            self.table_sample_query,
            self.table_sample_config,
            self.table_partition_config,
        ) = self._get_table_config()

        # add partition logic to test suite
        self.dfs = self.return_ometa_dataframes_sampled(
            service_connection_config=self.service_connection_config,
            client=get_connection(self.service_connection_config).client,
            table=self.table_entity,
            profile_sample_config=self.table_sample_config,
        )
        if self.dfs and self.table_partition_config:
            self.dfs = self.get_partitioned_df(self.dfs)

    def run_test_case(
        self,
        test_case: TestCase,
    ) -> Optional[TestCaseResult]:
        """Run table tests where platformsTest=OpenMetadata

        Args:
            test_case: test case object to execute

        Returns:
            TestCaseResult object
        """

        try:
            TestHandler = import_test_case_class(  # pylint: disable=invalid-name
                self.ometa_client.get_by_id(
                    TestDefinition, test_case.testDefinition.id
                ).entityType.value,
                "pandas",
                test_case.testDefinition.fullyQualifiedName,
            )

            test_handler = TestHandler(
                self.dfs,
                test_case=test_case,
                execution_date=datetime.now(tz=timezone.utc).timestamp(),
            )

            return Validator(validator_obj=test_handler).validate()
        except Exception as err:
            logger.error(
                f"Error executing {test_case.testDefinition.fullyQualifiedName} - {err}"
            )

            raise RuntimeError(err)
