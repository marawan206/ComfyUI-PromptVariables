from server import PromptServer

from .prompt_variables import PromptVariablesExtension, preview

WEB_DIRECTORY = "./js"

PromptServer.instance.routes.get("/prompt_variables/preview")(preview)


async def comfy_entrypoint():
    return PromptVariablesExtension()
