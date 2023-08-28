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
Helper methods for ES
"""

from typing import List, Optional, TypeVar

from pydantic import BaseModel

from metadata.generated.schema.analytics.reportData import ReportData
from metadata.generated.schema.entity.classification.tag import Tag
from metadata.generated.schema.entity.data.container import Container
from metadata.generated.schema.entity.data.dashboard import Dashboard
from metadata.generated.schema.entity.data.glossary import Glossary
from metadata.generated.schema.entity.data.glossaryTerm import GlossaryTerm
from metadata.generated.schema.entity.data.mlmodel import MlModel
from metadata.generated.schema.entity.data.pipeline import Pipeline
from metadata.generated.schema.entity.data.query import Query
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.entity.data.topic import Topic
from metadata.generated.schema.entity.teams.team import Team
from metadata.generated.schema.entity.teams.user import User
from metadata.utils.logger import utils_logger

logger = utils_logger()
T = TypeVar("T", bound=BaseModel)

ES_INDEX_MAP = {
    Table.__name__: "table_search_index",
    Team.__name__: "team_search_index",
    User.__name__: "user_search_index",
    Dashboard.__name__: "dashboard_search_index",
    Topic.__name__: "topic_search_index",
    Pipeline.__name__: "pipeline_search_index",
    Glossary.__name__: "glossary_search_index",
    GlossaryTerm.__name__: "glossary_search_index",
    MlModel.__name__: "mlmodel_search_index",
    Tag.__name__: "tag_search_index",
    Container.__name__: "container_search_index",
    Query.__name__: "query_search_index",
    ReportData.__name__: "entity_report_data_index",
    "web_analytic_user_activity_report": "web_analytic_user_activity_report_data_index",
    "web_analytic_entity_view_report": "web_analytic_entity_view_report_data_index",
}


def get_entity_from_es_result(
    entity_list: Optional[List[T]], fetch_multiple_entities: bool = False
) -> Optional[T]:
    """
    Return a single element from an entity list obtained
    from an ES query
    :param entity_list: ES query result
    :return: single entity
    """
    if entity_list and len(entity_list):
        if fetch_multiple_entities:
            return entity_list
        return entity_list[0]

    return None
