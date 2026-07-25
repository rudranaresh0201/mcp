from devmcp.prompts.base import Prompt
from devmcp.prompts.commit_message import CommitMessagePrompt
from devmcp.prompts.pr_description import PrDescriptionPrompt

ALL_PROMPTS: list[Prompt] = [CommitMessagePrompt(), PrDescriptionPrompt()]
PROMPTS_BY_NAME: dict[str, Prompt] = {p.name: p for p in ALL_PROMPTS}
