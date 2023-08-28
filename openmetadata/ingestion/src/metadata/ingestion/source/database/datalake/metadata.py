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
DataLake connector to fetch metadata from a files stored s3, gcs and Hdfs
"""
import traceback
from typing import Iterable, List, Optional, Tuple

from metadata.generated.schema.api.data.createDatabase import CreateDatabaseRequest
from metadata.generated.schema.api.data.createDatabaseSchema import (
    CreateDatabaseSchemaRequest,
)
from metadata.generated.schema.api.data.createTable import CreateTableRequest
from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.entity.data.databaseSchema import DatabaseSchema
from metadata.generated.schema.entity.data.table import (
    Column,
    DataType,
    Table,
    TableType,
)
from metadata.generated.schema.entity.services.connections.database.datalake.azureConfig import (
    AzureConfig,
)
from metadata.generated.schema.entity.services.connections.database.datalake.gcsConfig import (
    GCSConfig,
)
from metadata.generated.schema.entity.services.connections.database.datalake.s3Config import (
    S3Config,
)
from metadata.generated.schema.entity.services.connections.database.datalakeConnection import (
    DatalakeConnection,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.metadataIngestion.databaseServiceMetadataPipeline import (
    DatabaseServiceMetadataPipeline,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.models.ometa_classification import OMetaTagAndClassification
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.connections import get_connection
from metadata.ingestion.source.database.column_helpers import truncate_column_name
from metadata.ingestion.source.database.database_service import DatabaseServiceSource
from metadata.ingestion.source.database.datalake.columns import clean_dataframe
from metadata.readers.dataframe.models import DatalakeTableSchemaWrapper
from metadata.readers.dataframe.reader_factory import SupportedTypes
from metadata.utils import fqn
from metadata.utils.constants import COMPLEX_COLUMN_SEPARATOR, DEFAULT_DATABASE
from metadata.utils.datalake.datalake_utils import fetch_dataframe, get_file_format_type
from metadata.utils.filters import filter_by_schema, filter_by_table
from metadata.utils.logger import ingestion_logger
from metadata.utils.s3_utils import list_s3_objects

logger = ingestion_logger()

DATALAKE_DATA_TYPES = {
    **dict.fromkeys(["int64", "INT", "int32"], DataType.INT.value),
    "object": DataType.STRING.value,
    **dict.fromkeys(["float64", "float32", "float"], DataType.FLOAT.value),
    "bool": DataType.BOOLEAN.value,
    **dict.fromkeys(
        ["datetime64", "timedelta[ns]", "datetime64[ns]"], DataType.DATETIME.value
    ),
}


class DatalakeSource(DatabaseServiceSource):
    """
    Implements the necessary methods to extract
    Database metadata from Datalake Source
    """

    def __init__(self, config: WorkflowSource, metadata_config: OpenMetadataConnection):
        super().__init__()
        self.config = config
        self.source_config: DatabaseServiceMetadataPipeline = (
            self.config.sourceConfig.config
        )
        self.metadata_config = metadata_config
        self.metadata = OpenMetadata(metadata_config)
        self.service_connection = self.config.serviceConnection.__root__.config
        self.connection = get_connection(self.service_connection)

        self.client = self.connection.client
        self.table_constraints = None
        self.data_models = {}
        self.dbt_tests = {}
        self.database_source_state = set()

        self.connection_obj = self.connection
        self.test_connection()

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: DatalakeConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, DatalakeConnection):
            raise InvalidSourceException(
                f"Expected DatalakeConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def get_database_names(self) -> Iterable[str]:
        """
        Default case with a single database.

        It might come informed - or not - from the source.

        Sources with multiple databases should overwrite this and
        apply the necessary filters.
        """
        database_name = self.service_connection.databaseName or DEFAULT_DATABASE
        yield database_name

    def yield_database(self, database_name: str) -> Iterable[CreateDatabaseRequest]:
        """
        From topology.
        Prepare a database request and pass it to the sink
        """
        yield CreateDatabaseRequest(
            name=database_name,
            service=self.context.database_service.fullyQualifiedName,
        )

    def fetch_gcs_bucket_names(self):
        for bucket in self.client.list_buckets():
            schema_fqn = fqn.build(
                self.metadata,
                entity_type=DatabaseSchema,
                service_name=self.context.database_service.name.__root__,
                database_name=self.context.database.name.__root__,
                schema_name=bucket.name,
            )
            if filter_by_schema(
                self.config.sourceConfig.config.schemaFilterPattern,
                schema_fqn
                if self.config.sourceConfig.config.useFqnForFiltering
                else bucket.name,
            ):
                self.status.filter(schema_fqn, "Bucket Filtered Out")
                continue

            yield bucket.name

    def fetch_s3_bucket_names(self):
        for bucket in self.client.list_buckets()["Buckets"]:
            schema_fqn = fqn.build(
                self.metadata,
                entity_type=DatabaseSchema,
                service_name=self.context.database_service.name.__root__,
                database_name=self.context.database.name.__root__,
                schema_name=bucket["Name"],
            )
            if filter_by_schema(
                self.config.sourceConfig.config.schemaFilterPattern,
                schema_fqn
                if self.config.sourceConfig.config.useFqnForFiltering
                else bucket["Name"],
            ):
                self.status.filter(schema_fqn, "Bucket Filtered Out")
                continue
            yield bucket["Name"]

    def get_database_schema_names(self) -> Iterable[str]:
        """
        return schema names
        """
        bucket_name = self.service_connection.bucketName
        if isinstance(self.service_connection.configSource, GCSConfig):
            if bucket_name:
                yield bucket_name
            else:
                yield from self.fetch_gcs_bucket_names()

        if isinstance(self.service_connection.configSource, S3Config):
            if bucket_name:
                yield bucket_name
            else:
                yield from self.fetch_s3_bucket_names()

        if isinstance(self.service_connection.configSource, AzureConfig):
            yield from self.get_container_names()

    def get_container_names(self) -> Iterable[str]:
        """
        To get schema names
        """
        prefix = (
            self.service_connection.bucketName
            if self.service_connection.bucketName
            else ""
        )
        schema_names = self.client.list_containers(name_starts_with=prefix)
        for schema in schema_names:
            schema_fqn = fqn.build(
                self.metadata,
                entity_type=DatabaseSchema,
                service_name=self.context.database_service.name.__root__,
                database_name=self.context.database.name.__root__,
                schema_name=schema["name"],
            )
            if filter_by_schema(
                self.config.sourceConfig.config.schemaFilterPattern,
                schema_fqn
                if self.config.sourceConfig.config.useFqnForFiltering
                else schema["name"],
            ):
                self.status.filter(schema_fqn, "Container Filtered Out")
                continue

            yield schema["name"]

    def yield_database_schema(
        self, schema_name: str
    ) -> Iterable[CreateDatabaseSchemaRequest]:
        """
        From topology.
        Prepare a database schema request and pass it to the sink
        """
        yield CreateDatabaseSchemaRequest(
            name=schema_name,
            database=self.context.database.fullyQualifiedName,
        )

    def get_tables_name_and_type(  # pylint: disable=too-many-branches
        self,
    ) -> Iterable[Tuple[str, TableType]]:
        """
        Handle table and views.

        Fetches them up using the context information and
        the inspector set when preparing the db.

        :return: tables or views, depending on config
        """
        bucket_name = self.context.database_schema.name.__root__
        prefix = self.service_connection.prefix
        if self.source_config.includeTables:
            if isinstance(self.service_connection.configSource, GCSConfig):
                bucket = self.client.get_bucket(bucket_name)
                for key in bucket.list_blobs(prefix=prefix):
                    table_name = self.standardize_table_name(bucket_name, key.name)
                    # adding this condition as the gcp blobs also contains directory, which we can filter out
                    if table_name.endswith("/") or not self.check_valid_file_type(
                        key.name
                    ):
                        logger.debug(
                            f"Object filtered due to unsupported file type: {key.name}"
                        )
                        continue
                    table_fqn = fqn.build(
                        self.metadata,
                        entity_type=Table,
                        service_name=self.context.database_service.name.__root__,
                        database_name=self.context.database.name.__root__,
                        schema_name=self.context.database_schema.name.__root__,
                        table_name=table_name,
                        skip_es_search=True,
                    )

                    if filter_by_table(
                        self.config.sourceConfig.config.tableFilterPattern,
                        table_fqn
                        if self.config.sourceConfig.config.useFqnForFiltering
                        else table_name,
                    ):
                        self.status.filter(
                            table_fqn,
                            "Object Filtered Out",
                        )
                        continue

                    yield table_name, TableType.Regular
            if isinstance(self.service_connection.configSource, S3Config):
                kwargs = {"Bucket": bucket_name}
                if prefix:
                    kwargs["Prefix"] = prefix if prefix.endswith("/") else f"{prefix}/"
                for key in list_s3_objects(self.client, **kwargs):
                    table_name = self.standardize_table_name(bucket_name, key["Key"])
                    table_fqn = fqn.build(
                        self.metadata,
                        entity_type=Table,
                        service_name=self.context.database_service.name.__root__,
                        database_name=self.context.database.name.__root__,
                        schema_name=self.context.database_schema.name.__root__,
                        table_name=table_name,
                        skip_es_search=True,
                    )
                    if filter_by_table(
                        self.config.sourceConfig.config.tableFilterPattern,
                        table_fqn
                        if self.config.sourceConfig.config.useFqnForFiltering
                        else table_name,
                    ):
                        self.status.filter(
                            table_fqn,
                            "Object Filtered Out",
                        )
                        continue
                    if not self.check_valid_file_type(key["Key"]):
                        logger.debug(
                            f"Object filtered due to unsupported file type: {key['Key']}"
                        )
                        continue

                    yield table_name, TableType.Regular
            if isinstance(self.service_connection.configSource, AzureConfig):
                container_client = self.client.get_container_client(bucket_name)

                for file in container_client.list_blobs(
                    name_starts_with=prefix or None
                ):
                    table_name = self.standardize_table_name(bucket_name, file.name)
                    table_fqn = fqn.build(
                        self.metadata,
                        entity_type=Table,
                        service_name=self.context.database_service.name.__root__,
                        database_name=self.context.database.name.__root__,
                        schema_name=self.context.database_schema.name.__root__,
                        table_name=table_name,
                        skip_es_search=True,
                    )
                    if filter_by_table(
                        self.config.sourceConfig.config.tableFilterPattern,
                        table_fqn
                        if self.config.sourceConfig.config.useFqnForFiltering
                        else table_name,
                    ):
                        self.status.filter(
                            table_fqn,
                            "Object Filtered Out",
                        )
                        continue
                    if not self.check_valid_file_type(file.name):
                        logger.debug(
                            f"Object filtered due to unsupported file type: {file.name}"
                        )
                        continue
                    yield file.name, TableType.Regular

    def yield_table(
        self, table_name_and_type: Tuple[str, str]
    ) -> Iterable[Optional[CreateTableRequest]]:
        """
        From topology.
        Prepare a table request and pass it to the sink
        """
        table_name, table_type = table_name_and_type
        schema_name = self.context.database_schema.name.__root__
        try:
            table_constraints = None
            data_frame = fetch_dataframe(
                config_source=self.service_connection.configSource,
                client=self.client,
                file_fqn=DatalakeTableSchemaWrapper(
                    key=table_name,
                    bucket_name=schema_name,
                ),
            )

            # If no data_frame (due to unsupported type), ignore
            columns = self.get_columns(data_frame[0]) if data_frame else None
            if columns:
                table_request = CreateTableRequest(
                    name=table_name,
                    tableType=table_type,
                    columns=columns,
                    tableConstraints=table_constraints if table_constraints else None,
                    databaseSchema=self.context.database_schema.fullyQualifiedName,
                    fileFormat=get_file_format_type(table_name),
                )
                yield table_request
                self.register_record(table_request=table_request)
        except Exception as exc:
            error = f"Unexpected exception to yield table [{table_name}]: {exc}"
            logger.debug(traceback.format_exc())
            logger.warning(error)
            self.status.failed(table_name, error, traceback.format_exc())

    @staticmethod
    def _parse_complex_column(
        data_frame,
        column,
        final_column_list: List[Column],
        complex_col_dict: dict,
        processed_complex_columns: set,
    ) -> None:
        """
        This class parses the complex columns

        for example consider this data:
            {
                "level1": {
                    "level2":{
                        "level3": 1
                    }
                }
            }

        pandas would name this column as: _##level1_##level2_##level3
        (_## being the custom separator)

        this function would parse this column name and prepare a Column object like
        Column(
            name="level1",
            dataType="RECORD",
            children=[
                Column(
                    name="level2",
                    dataType="RECORD",
                    children=[
                        Column(
                            name="level3",
                            dataType="INT",
                        )
                    ]
                )
            ]
        )
        """
        try:
            # pylint: disable=bad-str-strip-call
            column_name = str(column).strip(COMPLEX_COLUMN_SEPARATOR)
            col_hierarchy = tuple(column_name.split(COMPLEX_COLUMN_SEPARATOR))
            parent_col: Optional[Column] = None
            root_col: Optional[Column] = None

            # here we are only processing col_hierarchy till [:-1]
            # because all the column/node before -1 would be treated
            # as a record and the column at -1 would be the column
            # having a primitive datatype
            # for example if col_hierarchy is ("image", "properties", "size")
            # then image would be the record having child properties which is
            # also a record  but the "size" will not be handled in this loop
            # as it will be of primitive type for ex. int
            for index, col_name in enumerate(col_hierarchy[:-1]):

                if complex_col_dict.get(col_hierarchy[: index + 1]):
                    # if we have already seen this column fetch that column
                    parent_col = complex_col_dict.get(col_hierarchy[: index + 1])
                else:
                    # if we have not seen this column than create the column and
                    # append to the parent if available
                    intermediate_column = Column(
                        name=truncate_column_name(col_name),
                        displayName=col_name,
                        dataType=DataType.RECORD.value,
                        children=[],
                        dataTypeDisplay=DataType.RECORD.value,
                    )
                    if parent_col:
                        parent_col.children.append(intermediate_column)
                        root_col = parent_col
                    parent_col = intermediate_column
                    complex_col_dict[col_hierarchy[: index + 1]] = parent_col

            # prepare the leaf node
            # use String as default type
            data_type = DataType.STRING.value
            if hasattr(data_frame[column], "dtypes"):
                data_type = DATALAKE_DATA_TYPES.get(
                    data_frame[column].dtypes.name, DataType.STRING.value
                )
            leaf_column = Column(
                name=col_hierarchy[-1],
                dataType=data_type,
                dataTypeDisplay=data_type,
            )
            parent_col.children.append(leaf_column)

            # finally add the top level node in the column list
            if col_hierarchy[0] not in processed_complex_columns:
                processed_complex_columns.add(col_hierarchy[0])
                final_column_list.append(root_col or parent_col)
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.warning(f"Unexpected exception parsing column [{column}]: {exc}")

    @staticmethod
    def fetch_col_types(data_frame, column_name):
        data_type = DATALAKE_DATA_TYPES.get(
            data_frame[column_name].dtypes.name, DataType.STRING.value
        )
        if data_type == DataType.FLOAT.value:
            try:
                if data_frame[column_name].dropna().any():
                    if isinstance(data_frame[column_name].iloc[0], dict):
                        return DataType.JSON.value
                    if isinstance(data_frame[column_name].iloc[0], str):
                        return DataType.STRING.value
            except Exception as err:
                logger.warning(
                    f"Failed to distinguish data type for column {column_name}, Falling back to {data_type}, exc: {err}"
                )
                logger.debug(traceback.format_exc())
        return data_type

    @staticmethod
    def get_columns(data_frame: "DataFrame"):
        """
        method to process column details
        """
        data_frame = clean_dataframe(data_frame)
        cols = []
        complex_col_dict = {}

        processed_complex_columns = set()
        if hasattr(data_frame, "columns"):
            df_columns = list(data_frame.columns)
            for column in df_columns:
                if COMPLEX_COLUMN_SEPARATOR in column:
                    DatalakeSource._parse_complex_column(
                        data_frame,
                        column,
                        cols,
                        complex_col_dict,
                        processed_complex_columns,
                    )
                else:
                    # use String by default
                    data_type = DataType.STRING.value
                    try:
                        if hasattr(data_frame[column], "dtypes"):
                            data_type = DatalakeSource.fetch_col_types(
                                data_frame, column_name=column
                            )

                        parsed_string = {
                            "dataTypeDisplay": data_type,
                            "dataType": data_type,
                            "name": truncate_column_name(column),
                            "displayName": column,
                        }
                        cols.append(Column(**parsed_string))
                    except Exception as exc:
                        logger.debug(traceback.format_exc())
                        logger.warning(
                            f"Unexpected exception parsing column [{column}]: {exc}"
                        )
        complex_col_dict.clear()
        return cols

    def yield_view_lineage(self) -> Iterable[AddLineageRequest]:
        yield from []

    def yield_tag(self, schema_name: str) -> Iterable[OMetaTagAndClassification]:
        pass

    def standardize_table_name(
        self, schema: str, table: str  # pylint: disable=unused-argument
    ) -> str:
        return table

    def check_valid_file_type(self, key_name):
        for supported_types in SupportedTypes:
            if key_name.endswith(supported_types.value):
                return True
        return False

    def close(self):
        if isinstance(self.service_connection.configSource, AzureConfig):
            self.client.close()
