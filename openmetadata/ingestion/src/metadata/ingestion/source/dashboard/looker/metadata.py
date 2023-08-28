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
Looker source module.
Supports:
- owner
- lineage
- usage

Notes:
- Filtering is applied on the Dashboard title or ID, if the title is missing
"""

import traceback
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Set, Type, Union, cast

from looker_sdk.sdk.api40.methods import Looker40SDK
from looker_sdk.sdk.api40.models import Dashboard as LookerDashboard
from looker_sdk.sdk.api40.models import (
    DashboardBase,
    DashboardElement,
    LookmlModel,
    LookmlModelExplore,
    LookmlModelNavExplore,
    Project,
)
from pydantic import ValidationError

from metadata.generated.schema.api.data.createChart import CreateChartRequest
from metadata.generated.schema.api.data.createDashboard import CreateDashboardRequest
from metadata.generated.schema.api.data.createDashboardDataModel import (
    CreateDashboardDataModelRequest,
)
from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.entity.data.chart import Chart
from metadata.generated.schema.entity.data.dashboard import (
    Dashboard as MetadataDashboard,
)
from metadata.generated.schema.entity.data.dashboardDataModel import (
    DashboardDataModel,
    DataModelType,
)
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.entity.services.connections.dashboard.lookerConnection import (
    LookerConnection,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.dashboardService import (
    DashboardServiceType,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.generated.schema.security.credentials.bitbucketCredentials import (
    BitBucketCredentials,
)
from metadata.generated.schema.security.credentials.githubCredentials import (
    GitHubCredentials,
)
from metadata.generated.schema.type.entityLineage import EntitiesEdge
from metadata.generated.schema.type.entityReference import EntityReference
from metadata.generated.schema.type.usageRequest import UsageRequest
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.source.dashboard.dashboard_service import (
    DashboardServiceSource,
    DashboardUsage,
)
from metadata.ingestion.source.dashboard.looker.columns import get_columns_from_model
from metadata.ingestion.source.dashboard.looker.links import get_path_from_link
from metadata.ingestion.source.dashboard.looker.models import (
    Includes,
    LookMlView,
    ViewName,
)
from metadata.ingestion.source.dashboard.looker.parser import LkmlParser
from metadata.readers.file.api_reader import ReadersCredentials
from metadata.readers.file.base import Reader
from metadata.readers.file.bitbucket import BitBucketReader
from metadata.readers.file.credentials import get_credentials_from_url
from metadata.readers.file.github import GitHubReader
from metadata.utils import fqn
from metadata.utils.filters import filter_by_chart, filter_by_datamodel
from metadata.utils.helpers import clean_uri, get_standard_chart_type
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


LIST_DASHBOARD_FIELDS = ["id", "title"]

# Here we can update the fields to get further information, such as:
# created_at, updated_at, last_updater_id, deleted_at, deleter_id, favorite_count, last_viewed_at
GET_DASHBOARD_FIELDS = [
    "id",
    "title",
    "dashboard_elements",
    "dashboard_filters",
    "view_count",
    "description",
    "folder",
    "user_id",  # Use as owner
]


def clean_dashboard_name(name: str) -> str:
    """
    Clean incorrect (and known) looker characters in ids
    """
    return name.replace("::", "_")


def build_datamodel_name(model_name: str, explore_name: str) -> str:
    """
    Build the explore name using the model name
    """
    return clean_dashboard_name(model_name + "_" + explore_name)


# pylint: disable=too-many-public-methods
class LookerSource(DashboardServiceSource):
    """
    Looker Source Class.

    Its client uses Looker 40 from the SDK: client = looker_sdk.init40()
    """

    config: WorkflowSource
    metadata_config: OpenMetadataConnection
    client: Looker40SDK

    def __init__(
        self,
        config: WorkflowSource,
        metadata_config: OpenMetadataConnection,
    ):
        super().__init__(config, metadata_config)
        self.today = datetime.now().strftime("%Y-%m-%d")

        self._explores_cache = {}
        self._repo_credentials: Optional[ReadersCredentials] = None
        self._reader_class: Optional[Type[Reader]] = None
        self._project_parsers: Optional[Dict[str, LkmlParser]] = None

    @classmethod
    def create(
        cls, config_dict: dict, metadata_config: OpenMetadataConnection
    ) -> "LookerSource":
        config = WorkflowSource.parse_obj(config_dict)
        connection: LookerConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, LookerConnection):
            raise InvalidSourceException(
                f"Expected LookerConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    @property
    def parser(self) -> Optional[Dict[str, LkmlParser]]:
        if self.repository_credentials:
            return self._project_parsers

        return None

    @parser.setter
    def parser(self, all_lookml_models: Sequence[LookmlModel]) -> None:
        """
        Initialize the project parsers.

        Each LookML model is linked to a Looker Project. Each project can be
        hosted in different GitHub repositories.

        Here we will prepare the Readers for each project and the LookML parser.

        We are assuming that each Git repo is based under the same owner
        and can be accessed with the same token. If we have
        any errors obtaining the git project information, we will default
        to the incoming GitHub Credentials.
        """
        if self.repository_credentials:
            all_projects: Set[str] = {model.project_name for model in all_lookml_models}
            self._project_parsers: Dict[str, LkmlParser] = {
                project_name: LkmlParser(
                    reader=self.reader(
                        credentials=self.get_lookml_project_credentials(
                            project_name=project_name
                        )
                    )
                )
                for project_name in all_projects
            }

            logger.info(f"We found the following parsers:\n {self._project_parsers}")

    def get_lookml_project_credentials(self, project_name: str) -> GitHubCredentials:
        """
        Given a lookml project, get its git URL and build the credentials
        """
        try:
            project: Project = self.client.project(project_id=project_name)
            return get_credentials_from_url(
                original=self.repository_credentials, url=project.git_remote_url
            )
        except Exception as err:
            logger.error(
                f"Error trying to build project credentials - [{err}]. We'll use the default ones."
            )
            return self.repository_credentials

    @property
    def reader(self) -> Optional[Type[Reader]]:
        """
        Depending on the type of the credentials we'll need a different reader
        """
        if not self._reader_class:
            if self.service_connection.gitCredentials and isinstance(
                self.service_connection.gitCredentials, GitHubCredentials
            ):
                self._reader_class = GitHubReader

            if self.service_connection.gitCredentials and isinstance(
                self.service_connection.gitCredentials, BitBucketCredentials
            ):
                self._reader_class = BitBucketReader

        return self._reader_class

    @property
    def repository_credentials(self) -> Optional[ReadersCredentials]:
        """
        Check if the credentials are informed and return them.

        We either get GitHubCredentials or `NoGitHubCredentials`
        """
        if not self._repo_credentials:
            if self.service_connection.gitCredentials and isinstance(
                self.service_connection.gitCredentials, GitHubCredentials
            ):
                self._repo_credentials = self.service_connection.gitCredentials

        return self._repo_credentials

    def list_datamodels(self) -> Iterable[LookmlModelExplore]:
        """
        Fetch explores with the SDK
        """
        if self.source_config.includeDataModels:
            # First, pick up all the LookML Models
            try:
                all_lookml_models: Sequence[
                    LookmlModel
                ] = self.client.all_lookml_models()

                # Then, gather their information and build the parser
                self.parser = all_lookml_models

                # Finally, iterate through them to ingest Explores and Views
                yield from self.fetch_lookml_explores(all_lookml_models)
            except Exception as err:
                logger.debug(traceback.format_exc())
                logger.error(f"Unexpected error fetching LookML models - {err}")

    def fetch_lookml_explores(
        self, all_lookml_models: Sequence[LookmlModel]
    ) -> Iterable[LookmlModelExplore]:
        """
        Based on the LookML models, iterate over the explores
        they contain and filter if needed
        """
        # Then, fetch the explores for each of them
        for lookml_model in all_lookml_models:
            # Each LookML model have a list of explores we'll be ingesting
            for explore_nav in (
                cast(Sequence[LookmlModelNavExplore], lookml_model.explores) or []
            ):
                if filter_by_datamodel(
                    self.source_config.dataModelFilterPattern, lookml_model.name
                ):
                    self.status.filter(
                        lookml_model.name, "Data model (Explore) filtered out."
                    )
                    continue

                try:
                    explore = self.client.lookml_model_explore(
                        lookml_model_name=lookml_model.name,
                        explore_name=explore_nav.name,
                    )
                    yield explore
                except Exception as err:
                    logger.debug(traceback.format_exc())
                    logger.warning(
                        f"Error fetching LookML Explore [{explore_nav.name}] in model [{lookml_model.name}] - {err}"
                    )

    def yield_bulk_datamodel(
        self, model: LookmlModelExplore
    ) -> Optional[Iterable[CreateDashboardDataModelRequest]]:
        """
        Get the Explore and View information and prepare
        the model creation request
        """
        try:
            datamodel_name = build_datamodel_name(model.model_name, model.name)
            if filter_by_datamodel(
                self.source_config.dataModelFilterPattern, datamodel_name
            ):
                self.status.filter(datamodel_name, "Data model filtered out.")
            else:
                explore_datamodel = CreateDashboardDataModelRequest(
                    name=datamodel_name,
                    displayName=model.name,
                    description=model.description,
                    service=self.context.dashboard_service.fullyQualifiedName.__root__,
                    dataModelType=DataModelType.LookMlExplore.value,
                    serviceType=DashboardServiceType.Looker.value,
                    columns=get_columns_from_model(model),
                    sql=self._get_explore_sql(model),
                    # In Looker, you need to create Explores and Views within a Project
                    project=model.project_name,
                )
                yield explore_datamodel
                self.status.scanned(f"Data Model Scanned: {model.name}")

                # Maybe use the project_name as key too?
                # Save the explores for when we create the lineage with the dashboards and views
                self._explores_cache[
                    explore_datamodel.name.__root__
                ] = self.context.dataModel  # This is the newly created explore

                # We can get VIEWs from the JOINs to know the dependencies
                # We will only try and fetch if we have the credentials
                if self.repository_credentials:
                    for view in model.joins:
                        if filter_by_datamodel(
                            self.source_config.dataModelFilterPattern, view.name
                        ):
                            self.status.filter(
                                view.name, "Data model (View) filtered out."
                            )
                            continue

                        yield from self._process_view(
                            view_name=ViewName(view.name), explore=model
                        )

        except ValidationError as err:
            error = f"Validation error yielding Data Model [{model.name}]: {err}"
            logger.debug(traceback.format_exc())
            logger.error(error)
            self.status.failed(
                name=model.name, error=error, stack_trace=traceback.format_exc()
            )
        except Exception as err:
            error = f"Wild error yielding Data Model [{model.name}]: {err}"
            logger.debug(traceback.format_exc())
            logger.error(error)
            self.status.failed(
                name=model.name, error=error, stack_trace=traceback.format_exc()
            )

    def _get_explore_sql(self, explore: LookmlModelExplore) -> Optional[str]:
        """
        If github creds are sent, we can pick the explore
        file definition and add it here
        """
        # Only look to parse if creds are in
        if self.repository_credentials:
            try:
                project_parser = self.parser.get(explore.project_name)
                if project_parser:
                    # This will only parse if the file has not been parsed yet
                    project_parser.parse_file(
                        Includes(get_path_from_link(explore.lookml_link))
                    )
                    return project_parser.parsed_files.get(
                        Includes(get_path_from_link(explore.lookml_link))
                    )
            except Exception as err:
                logger.warning(f"Exception getting the model sql: {err}")

        return None

    def _process_view(self, view_name: ViewName, explore: LookmlModelExplore):
        """
        For each view referenced in the JOIN of the explore,
        We first load the explore file from GitHub, then:
        1. Fetch the view from the GitHub files (search in includes)
        2. Yield the view as a dashboard Model
        3. Yield the lineage between the View -> Explore and Source -> View
        Every visited view, will be cached so that we don't need to process
        everything again.
        """

        project_parser = self.parser.get(explore.project_name)
        if project_parser:
            view: Optional[LookMlView] = project_parser.find_view(
                view_name=view_name,
                path=Includes(get_path_from_link(explore.lookml_link)),
            )

            if view:
                yield CreateDashboardDataModelRequest(
                    name=build_datamodel_name(explore.model_name, view.name),
                    displayName=view.name,
                    description=view.description,
                    service=self.context.dashboard_service.fullyQualifiedName.__root__,
                    dataModelType=DataModelType.LookMlView.value,
                    serviceType=DashboardServiceType.Looker.value,
                    columns=get_columns_from_model(view),
                    sql=project_parser.parsed_files.get(Includes(view.source_file)),
                    # In Looker, you need to create Explores and Views within a Project
                    project=explore.project_name,
                )
                self.status.scanned(f"Data Model Scanned: {view.name}")

                yield from self.add_view_lineage(view, explore)

    def add_view_lineage(
        self, view: LookMlView, explore: LookmlModelExplore
    ) -> Iterable[AddLineageRequest]:
        """
        Add the lineage source -> view -> explore
        """
        try:
            # This is the name we store in the cache
            explore_name = build_datamodel_name(explore.model_name, explore.name)
            explore_model = self._explores_cache.get(explore_name)

            # TODO: column-level lineage parsing the explore columns with the format `view_name.col`
            # Now the context has the newly created view
            if explore_model:
                yield self._get_add_lineage_request(
                    from_entity=self.context.dataModel, to_entity=explore_model
                )

            else:
                logger.info(
                    f"Could not find model for explore [{explore.model_name}: {explore.name}] in the cache"
                    " while processing view lineage."
                )

            if view.sql_table_name:
                source_table_name = self._clean_table_name(view.sql_table_name)

                # View to the source is only there if we are informing the dbServiceNames
                for db_service_name in self.source_config.dbServiceNames or []:
                    yield self.build_lineage_request(
                        source=source_table_name,
                        db_service_name=db_service_name,
                        to_entity=self.context.dataModel,
                    )

        except Exception as err:
            logger.debug(traceback.format_exc())
            logger.error(
                f"Error to yield lineage details for view [{view.name}]: {err}"
            )

    def get_dashboards_list(self) -> List[DashboardBase]:
        """
        Get List of all dashboards
        """
        try:
            return list(
                self.client.all_dashboards(fields=",".join(LIST_DASHBOARD_FIELDS))
            )
        except Exception as err:
            logger.debug(traceback.format_exc())
            logger.error(f"Wild error trying to obtain dashboard list {err}")
            # If we cannot list the dashboards, let's blow up
            raise err

    def get_dashboard_name(self, dashboard: DashboardBase) -> str:
        """
        Get Dashboard Title. This will be used for filtering.
        If the title is not present, we'll send the ID
        """
        return dashboard.title or dashboard.id

    def get_dashboard_details(self, dashboard: DashboardBase) -> LookerDashboard:
        """
        Get Dashboard Details
        """
        return self.client.dashboard(
            dashboard_id=dashboard.id, fields=",".join(GET_DASHBOARD_FIELDS)
        )

    def get_owner_details(
        self, dashboard_details: LookerDashboard
    ) -> Optional[EntityReference]:
        """Get dashboard owner

        Store the visited users in the _owners_ref cache, even if we found them
        in OM or not.

        If the user has not yet been visited, store it and return from cache.

        Args:
            dashboard_details: LookerDashboard
        Returns:
            Optional[EntityReference]
        """
        try:
            if dashboard_details.user_id is not None:
                dashboard_owner = self.client.user(dashboard_details.user_id)
                user = self.metadata.get_user_by_email(dashboard_owner.email)
                if user:
                    return EntityReference(id=user.id.__root__, type="user")

        except Exception as err:
            logger.debug(traceback.format_exc())
            logger.warning(f"Could not fetch owner data due to {err}")

        return None

    def yield_dashboard(
        self, dashboard_details: LookerDashboard
    ) -> CreateDashboardRequest:
        """
        Method to Get Dashboard Entity
        """
        dashboard_request = CreateDashboardRequest(
            name=clean_dashboard_name(dashboard_details.id),
            displayName=dashboard_details.title,
            description=dashboard_details.description or None,
            charts=[
                fqn.build(
                    self.metadata,
                    entity_type=Chart,
                    service_name=self.context.dashboard_service.fullyQualifiedName.__root__,
                    chart_name=chart.name.__root__,
                )
                for chart in self.context.charts
            ],
            # Dashboards are created from the UI directly. They are not linked to a project
            # like LookML assets, but rather just organised in folders.
            project=self._get_dashboard_project(dashboard_details),
            sourceUrl=f"{clean_uri(self.service_connection.hostPort)}/dashboards/{dashboard_details.id}",
            service=self.context.dashboard_service.fullyQualifiedName.__root__,
        )
        yield dashboard_request
        self.register_record(dashboard_request=dashboard_request)

    @staticmethod
    def _get_dashboard_project(dashboard_details: LookerDashboard) -> Optional[str]:
        """
        Get dashboard project if the folder is informed
        """
        try:
            return dashboard_details.folder.name
        except Exception as exc:
            logger.debug(
                f"Cannot get folder name from dashboard [{dashboard_details.title}] - [{exc}]"
            )
            return None

    @staticmethod
    def _clean_table_name(table_name: str) -> str:
        """
        sql_table_names might be renamed when defining
        an explore. E.g., customers as cust
        :param table_name: explore table name
        :return: clean table name
        """

        return table_name.lower().split("as")[0].strip()

    @staticmethod
    def get_dashboard_sources(dashboard_details: LookerDashboard) -> Set[str]:
        """
        Set explores to build lineage for the processed dashboard
        """
        dashboard_sources: Set[str] = set()

        for chart in cast(
            Iterable[DashboardElement], dashboard_details.dashboard_elements
        ):
            if chart.query and chart.query.view:
                dashboard_sources.add(
                    build_datamodel_name(chart.query.model, chart.query.view)
                )
            if chart.look and chart.look.query and chart.look.query.view:
                dashboard_sources.add(
                    build_datamodel_name(chart.look.query.model, chart.look.query.view)
                )
            if (
                chart.result_maker
                and chart.result_maker.query
                and chart.result_maker.query.view
            ):
                dashboard_sources.add(
                    build_datamodel_name(
                        chart.result_maker.query.model, chart.result_maker.query.view
                    )
                )

        return dashboard_sources

    def get_explore(self, explore_name: str) -> Optional[DashboardDataModel]:
        """
        Get the dashboard model from cache or API
        """
        return self._explores_cache.get(explore_name) or self.metadata.get_by_name(
            entity=DashboardDataModel,
            fqn=fqn.build(
                self.metadata,
                entity_type=DashboardDataModel,
                service_name=self.context.dashboard_service.fullyQualifiedName.__root__,
                data_model_name=explore_name,
            ),
        )

    def yield_dashboard_lineage_details(
        self, dashboard_details: LookerDashboard, _: str
    ) -> Optional[Iterable[AddLineageRequest]]:
        """
        Get lineage between charts and data sources.

        We look at:
        - chart.query
        - chart.look (chart.look.query)
        - chart.result_maker
        """

        try:
            source_explore_list = self.get_dashboard_sources(dashboard_details)
            for explore_name in source_explore_list:
                cached_explore = self.get_explore(explore_name)
                if cached_explore:
                    yield AddLineageRequest(
                        edge=EntitiesEdge(
                            fromEntity=EntityReference(
                                id=cached_explore.id.__root__,
                                type="dashboardDataModel",
                            ),
                            toEntity=EntityReference(
                                id=self.context.dashboard.id.__root__,
                                type="dashboard",
                            ),
                        )
                    )

        except Exception as exc:
            error = f"Unexpected exception yielding lineage from [{self.context.dashboard.displayName}]: {exc}"
            logger.debug(traceback.format_exc())
            logger.warning(error)
            self.status.failed(
                self.context.dashboard.displayName, error, traceback.format_exc()
            )

    def build_lineage_request(
        self,
        source: str,
        db_service_name: str,
        to_entity: Union[MetadataDashboard, DashboardDataModel],
    ) -> Optional[AddLineageRequest]:
        """
        Once we have a list of origin data sources, check their components
        and build the lineage request.

        We will try searching in ES with and without the `database`

        Args:
            source: table name from the source list
            db_service_name: name of the service from the config
            to_entity: Dashboard Entity being used
        """

        source_elements = fqn.split_table_name(table_name=source)

        for database_name in [source_elements["database"], None]:
            from_fqn = fqn.build(
                self.metadata,
                entity_type=Table,
                service_name=db_service_name,
                database_name=database_name,
                schema_name=source_elements["database_schema"],
                table_name=source_elements["table"],
            )

            from_entity: Table = self.metadata.get_by_name(
                entity=Table,
                fqn=from_fqn,
            )

            if from_entity:
                return self._get_add_lineage_request(
                    to_entity=to_entity, from_entity=from_entity
                )

        return None

    def yield_dashboard_chart(
        self, dashboard_details: LookerDashboard
    ) -> Optional[Iterable[CreateChartRequest]]:
        """
        Method to fetch charts linked to dashboard
        """
        for chart in dashboard_details.dashboard_elements:
            try:
                if filter_by_chart(
                    chart_filter_pattern=self.source_config.chartFilterPattern,
                    chart_name=chart.id,
                ):
                    self.status.filter(chart.id, "Chart filtered out")
                    continue

                if not chart.id:
                    logger.debug(f"Found chart {chart} without id. Skipping.")
                    continue

                yield CreateChartRequest(
                    name=chart.id,
                    displayName=chart.title or chart.id,
                    description=self.build_chart_description(chart) or None,
                    chartType=get_standard_chart_type(chart.type).value,
                    sourceUrl=chart.query.share_url
                    if chart.query is not None
                    else f"{clean_uri(self.service_connection.hostPort)}/merge?mid={chart.merge_result_id}",
                    service=self.context.dashboard_service.fullyQualifiedName.__root__,
                )
                self.status.scanned(chart.id)

            except Exception as exc:
                logger.debug(traceback.format_exc())
                logger.warning(f"Error creating chart [{chart}]: {exc}")

    @staticmethod
    def build_chart_description(chart: DashboardElement) -> Optional[str]:
        """
        Chart descriptions will be based on the subtitle + note_text, if exists.
        If the chart is a text tile, we will add the text as the chart description as well.
        This should keep the dashboard searchable without breaking the original metadata structure.
        """

        # If the string is None or empty, filter it out.
        try:
            return "; ".join(
                filter(
                    lambda string: string,
                    [chart.subtitle_text, chart.body_text, chart.note_text],
                )
                or []
            )
        except Exception as err:
            logger.debug(traceback.format_exc())
            logger.error(f"Error getting chart description: {err}")
            return None

    def yield_dashboard_usage(  # pylint: disable=W0221
        self, dashboard_details: LookerDashboard
    ) -> Optional[DashboardUsage]:
        """
        The dashboard.view_count gives us the total number of views. However, we need to
        pass the views for each day (execution).

        In this function we will first validate if the usageSummary
        returns us some usage for today's date. If so, we will stop the
        execution.

        Otherwise, we will add the difference between the usage from the last time
        the usage was reported and today's view_count from the dashboard.

        Example usage summary from OM API:
        "usageSummary": {
            "dailyStats": {
                "count": 51,
                "percentileRank": 0.0
            },
            "date": "2022-06-23",
            "monthlyStats": {
                "count": 105,
                "percentileRank": 0.0
            },
            "weeklyStats": {
                "count": 105,
                "percentileRank": 0.0
            }
        },
        :param dashboard_details: Looker Dashboard
        :return: UsageRequest, if not computed
        """

        dashboard: MetadataDashboard = self.context.dashboard

        try:
            current_views = dashboard_details.view_count

            if not current_views:
                logger.debug(f"No usage to report for {dashboard_details.title}")

            if not dashboard.usageSummary:
                logger.info(
                    f"Yielding fresh usage for {dashboard.fullyQualifiedName.__root__}"
                )
                yield DashboardUsage(
                    dashboard=dashboard,
                    usage=UsageRequest(date=self.today, count=current_views),
                )

            elif (
                str(dashboard.usageSummary.date.__root__) != self.today
                or not dashboard.usageSummary.dailyStats.count
            ):
                latest_usage = dashboard.usageSummary.dailyStats.count

                new_usage = current_views - latest_usage
                if new_usage < 0:
                    raise ValueError(
                        f"Wrong computation of usage difference. Got new_usage={new_usage}."
                    )

                logger.info(
                    f"Yielding new usage for {dashboard.fullyQualifiedName.__root__}"
                )
                yield DashboardUsage(
                    dashboard=dashboard,
                    usage=UsageRequest(
                        date=self.today, count=current_views - latest_usage
                    ),
                )

            else:
                logger.debug(
                    f"Latest usage {dashboard.usageSummary} vs. today {self.today}. Nothing to compute."
                )
                logger.info(
                    f"Usage already informed for {dashboard.fullyQualifiedName.__root__}"
                )

        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Exception computing dashboard usage for {dashboard.fullyQualifiedName.__root__}: {exc}"
            )
