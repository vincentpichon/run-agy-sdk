# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import os
import re
import sys

# Attempt to import Antigravity SDK
try:
    from google.antigravity import Agent, LocalAgentConfig, types
    from google.antigravity.hooks import policy
except ImportError:
    print(
        "::error::Google Antigravity SDK is not installed. Please run 'pip install google-antigravity' first."
    )
    sys.exit(1)

# Robust TOML Parser with fallback
try:
    import tomllib


    def load_toml(f) -> dict:
        return tomllib.load(f)
except ImportError:
    try:
        import tomli


        def load_toml(f) -> dict:
            return tomli.load(f)
    except ImportError:
        # Fall back to a simple regex parser if neither is available
        class SimpleTomlParser:
            @staticmethod
            def load(f) -> dict:
                content = f.read().decode("utf-8")
                data = {}
                # Match triple-quoted prompt = """..."""
                prompt_match = re.search(
                    r'prompt\s*=\s*"""(.*?)"""', content, re.DOTALL
                )
                if prompt_match:
                    data["prompt"] = prompt_match.group(1).strip()
                # Match single/double-quoted description
                desc_match = re.search(r'description\s*=\s*["\'](.*?)["\']', content)
                if desc_match:
                    data["description"] = desc_match.group(1).strip()
                return data


        def load_toml(f) -> dict:
            return SimpleTomlParser.load(f)


# Commands that only analyse GitHub content and must not write anything
READ_ONLY_COMMANDS = {"antigravity-plan"}

# Discussion read tools (github-mcp-server "discussions" toolset)
DISCUSSION_READ_TOOLS = ["get_discussion", "get_discussion_comments"]

# Only GitHub MCP tools allowed for read-only commands (allowlist)
GITHUB_READ_TOOLS = {
    "issue_read",
    "list_issues",
    "search_issues",
    "pull_request_read",
    "list_pull_requests",
    "search_pull_requests",
    "get_commit",
    "list_commits",
    "get_file_contents",
    "search_code",
    *DISCUSSION_READ_TOOLS,
}


async def main() -> None:
    # 1. Retrieve environment variables
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTIGRAVITY_API_KEY")
    github_token = os.environ.get("GITHUB_TOKEN") or os.environ.get(
        "GITHUB_PERSONAL_ACCESS_TOKEN"
    )
    pr_number = os.environ.get("PULL_REQUEST_NUMBER") or os.environ.get(
        "GITHUB_PR_NUMBER"
    )
    repository = os.environ.get("REPOSITORY") or os.environ.get("GITHUB_REPOSITORY")
    additional_context = os.environ.get("ADDITIONAL_CONTEXT", "")
    prompt_text = os.environ.get("PROMPT", "").strip()
    trust_workspace = os.environ.get("TRUST_WORKSPACE") == "true"
    jira_mcp_api_token = os.environ.get("JIRA_API_TOKEN", False)
    additional_gh_tools = [
        tool.strip()
        for tool in os.environ.get("ADDITIONAL_GH_TOOLS", "").split(",")
        if tool.strip()
    ]
    item_type = os.environ.get("ITEM_TYPE", "").strip().lower()
    item_number = os.environ.get("ITEM_NUMBER", "").strip()

    if not api_key:
        print(
            "::error::API key is missing. Please set the 'api-key' input or GEMINI_API_KEY env var."
        )
        sys.exit(1)

    if not github_token:
        print(
            "::error::GitHub token is missing. Please set the 'github-token' input or GITHUB_TOKEN env var."
        )
        sys.exit(1)

    # Export GITHUB_PERSONAL_ACCESS_TOKEN to ensure the child MCP process inherits it
    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = github_token

    # 2. TOML Command Parsing & Variable Interpolation
    system_instructions = "You are a helpful software engineering assistant."
    chat_prompt = prompt_text
    command_name = prompt_text[1:] if prompt_text.startswith("/") else ""
    read_only_command = command_name in READ_ONLY_COMMANDS

    if read_only_command:
        if item_type not in ("issue", "discussion"):
            print(
                f"::error::Command /{command_name} requires 'item_type' to be 'issue' or 'discussion'."
            )
            sys.exit(1)
        if not re.fullmatch(r"[1-9][0-9]*", item_number):
            print(
                f"::error::Command /{command_name} requires a numeric 'item_number'."
            )
            sys.exit(1)

    if prompt_text.startswith("/"):
        # Prevent directory traversal attacks
        if ".." in command_name or "/" in command_name or "\\" in command_name:
            print(
                f"::error::Invalid custom command name contains path separators: {command_name}"
            )
            sys.exit(1)

        action_path = os.environ.get("GITHUB_ACTION_PATH") or ""
        command_file = os.path.join(
            action_path, ".github", "commands", f"{command_name}.toml"
        )

        if os.path.exists(command_file):
            print(f"Loading custom command config from {command_file}...")
            with open(command_file, "rb") as f:
                command_data = load_toml(f)

            raw_prompt = command_data.get("prompt", "")

            # Substitute variables dynamically from the environment
            env_context = dict(os.environ)
            env_context.setdefault("REPOSITORY", repository or "")
            env_context.setdefault("PULL_REQUEST_NUMBER", pr_number or "")
            env_context.setdefault(
                "ISSUE_NUMBER", os.environ.get("GITHUB_ISSUE_NUMBER", "")
            )
            env_context.setdefault("ADDITIONAL_CONTEXT", additional_context or "")

            def replace_env_var(match: re.Match) -> str:
                var_name = match.group(1)
                # Return the value from env or a fallback empty string
                return env_context.get(var_name, "")

            interpolated_prompt = re.sub(
                r"!\{\s*echo\s+\$([A-Za-z0-9_]+)\s*\}", replace_env_var, raw_prompt
            )

            system_instructions = interpolated_prompt

            # Since system_instructions has all details, prompt chat to run the review
            chat_prompt = f"Run the command: {prompt_text}"
        else:
            print(
                f"Warning: Custom command configuration file {command_file} not found. Running with default context."
            )

    # 3. MCP Server Configuration
    # We configure the GitHub stdio MCP server for GitHub operations

    enabled_tools = [
        "add_comment_to_pending_review",
        "pull_request_read",
        "pull_request_review_write",
        "add_issue_comment",
        "issue_read",
        "list_issues",
        "search_issues",
        "list_pull_requests",
        "search_pull_requests",
        "get_commit",
        "get_file_contents",
        "list_commits",
        "search_code",
        "create_pull_request"
    ]
    enabled_tools.extend(additional_gh_tools)
    if read_only_command:
        if item_type == "discussion":
            enabled_tools.extend(DISCUSSION_READ_TOOLS)
        # Read-only commands only get read tools, whatever the caller allows
        enabled_tools = [tool for tool in enabled_tools if tool in GITHUB_READ_TOOLS]
    # Deduplicate while preserving order
    enabled_tools = list(dict.fromkeys(enabled_tools))

    docker_args = [
        "run",
        "-i",
        "--rm",
        "-e",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
    ]
    if any(tool in DISCUSSION_READ_TOOLS for tool in enabled_tools):
        # Discussion tools are not part of the server's default toolsets
        docker_args += ["-e", "GITHUB_TOOLSETS=default,discussions"]
    if read_only_command:
        # Make the server itself refuse write tools
        docker_args += ["-e", "GITHUB_READ_ONLY=1"]
    docker_args.append("ghcr.io/github/github-mcp-server:v0.27.0")

    github_mcp = types.McpStdioServer(
        name="github",
        command="docker",
        args=docker_args,
        enabled_tools=enabled_tools,
    )
    mcp_servers = [github_mcp]
    # 4. Declarative Safety Policies
    if prompt_text.startswith("/"):
        # For reviews or automated tasks, deny shell/arbitrary code execution completely
        # and only allow safe MCP/GitHub tools and file views.
        policies = [
            policy.deny_all(),
            policy.allow(github_mcp),
            policy.allow("view_file"),
            policy.allow("find_file"),
        ]
    else:
        # For general goals, respect trust_workspace setting
        if trust_workspace:
            policies = [policy.allow_all()]
        else:
            policies = policy.confirm_run_command()

    if jira_mcp_api_token:
        jira_mcp = types.McpStdioServer(
            name="jira",
            command="docker",
            args=[
                "run",
                "-i",
                "--rm",
                "-e",
                "JIRA_URL",
                "-e",
                "JIRA_EMAIL"
                "-e",
                "JIRA_API_TOKEN",
                "ghcr.io/github/github-mcp-server:v0.27.0",
            ],
            enabled_tools=["jira_read", "jira_schema"],
        )
        policies.append(policy.allow(jira_mcp))
        mcp_servers.append(jira_mcp)

    # 5. Initialize and run Agent
    config = LocalAgentConfig(
        api_key=api_key,
        system_instructions=system_instructions,
        mcp_servers=mcp_servers,
        policies=policies,
        workspaces=[os.getcwd()],
        capabilities=types.CapabilitiesConfig(
            enable_subagents=True,
            disabled_tools=[
                types.BuiltinTools.ASK_QUESTION,
            ]),
    )

    print("Starting Antigravity Agent...")
    async with Agent(config) as agent:
        response = await agent.chat(chat_prompt)

        print("\n::: Agent Session Execution :::")

        print("  Agent (Streaming thoughts):")
        print("  -------------------------------------------------------")
        async for thought in response.thoughts:
            print(thought, end="", flush=True)
        print("\n  -------------------------------------------------------\n")

        print("  Agent (Streaming final answer):")
        full_response_text = []
        async for token in response:
            sys.stdout.write(token)
            sys.stdout.flush()
            full_response_text.append(token)
        print("\n::: End of Agent Session Execution :::")

        # Write to GITHUB_OUTPUT file if available to satisfy composite action output contract
        github_output_path = os.environ.get("GITHUB_OUTPUT")
        if github_output_path:
            full_text = "".join(full_response_text)
            try:
                import uuid

                delimiter = f"EOF_{uuid.uuid4().hex}"
                with open(github_output_path, "a", encoding="utf-8") as f:
                    # Multi-line syntax for GITHUB_OUTPUT
                    f.write(f"response<<{delimiter}\n{full_text}\n{delimiter}\n")
                    f.write("stats={}\n")  # placeholder for execution statistics
            except Exception as e:
                print(
                    f"Warning: Failed to write to GITHUB_OUTPUT: {e}", file=sys.stderr
                )


if __name__ == "__main__":
    asyncio.run(main())
