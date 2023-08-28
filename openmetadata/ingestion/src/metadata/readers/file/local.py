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
Local Reader
"""
import traceback
from pathlib import Path
from typing import List, Optional, Union

from metadata.readers.file.base import Reader, ReadException
from metadata.utils.constants import UTF_8
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class LocalReader(Reader):
    """
    Read files locally
    """

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or Path(__file__)

    def read(self, path: str, **kwargs) -> Union[str, bytes]:
        """
        simple local reader

        If we cannot encode the file contents, we fallback and returns the bytes
        to let the client use this data as needed.
        """
        try:
            with open(self.base_path / path, encoding=UTF_8) as file:
                return file.read()

        except UnicodeDecodeError:
            logger.debug(
                "Cannot read the file with UTF-8 encoding. Trying to read bytes..."
            )
            with open(self.base_path / path, "rb") as file:
                return file.read()

        except Exception as err:
            logger.debug(traceback.format_exc())
            raise ReadException(f"Error reading file [{path}] locally: {err}")

    def _get_tree(self) -> Optional[List[str]]:
        """
        Return the tree with the files relative to the base path
        """
        return [
            str(path).replace(str(self.base_path) + "/", "")
            for path in Path(self.base_path).rglob("*")
        ]
