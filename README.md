# AGY SDK GitHub Action

`run-agy-sdk` is a composite GitHub Action demonstrating how to run the Antigravity Python SDK (`google-antigravity`) for automated code reviews and task execution. Because it is a composite action running directly on the host, it allows the SDK to spawn Docker-based MCP servers (such as `github-mcp-server`) directly on the host runner.

## ⚙️ Workflow Installation & Setup

To use `run-agy-sdk` in another repository, you need to configure a GitHub Actions workflow that references this action. Follow the instructions below to set up automated reviews or comment-based on-demand reviews.

### 1. Provision API Key Secrets

The Antigravity action requires a Google Gemini or Antigravity API key to authenticate language model interactions.

1. Generate your API key.
2. In your target repository, go to **Settings** > **Secrets and variables** > **Actions**.
3. Create a new Repository Secret named `ANTIGRAVITY_API_KEY` and paste your API key as the value.

### 2. Configure the GitHub Actions Workflow

Create a new file in your repository at `.github/workflows/antigravity-review.yml` and copy the following configuration:

```yaml
name: '🔎 Antigravity PR Review'

on:
  pull_request:
    types: [opened, reopened]
  workflow_dispatch: # Allows manual trigger from the Actions tab

concurrency:
  group: '${{ github.workflow }}-${{ github.event.pull_request.number || github.ref_name }}'
  cancel-in-progress: true

jobs:
  antigravity-review:
    runs-on: 'ubuntu-latest'
    timeout-minutes: 20
    
    # Required permissions for the action to read contents and post PR comments/feedback
    permissions:
      contents: 'read'
      pull-requests: 'write'
      issues: 'write'

    steps:
      - name: 'Checkout Repository'
        uses: 'actions/checkout@v6'
        with:
          persist-credentials: false

      - name: 'Run Antigravity PR Review'
        # Reference this action remotely from its repository
        uses: 'rsamborski/run-agy-sdk@main'
        id: 'agy_pr_review'
        with:
          api-key: '${{ secrets.ANTIGRAVITY_API_KEY }}'
          github-token: '${{ secrets.GITHUB_TOKEN || github.token }}'
          mode: 'review'
          prompt: '/antigravity-review'
          trust-workspace: 'true'
          sandbox-profile: 'true'
```

> [!TIP]
> For production environments, it is recommended to lock the action version to a specific commit SHA (e.g., `rsamborski/run-agy-sdk@<commit-sha>`) rather than `@main` to prevent unexpected breaks from upstream updates.

For a complete workflow template that supports both **Automated PR Auditing** (runs automatically on code updates) and **Comment-Triggered Reviews** (triggered via PR comments), please refer to the reference workflow in [.github/workflows/antigravity-autonomous-review.yml](.github/workflows/antigravity-autonomous-review.yml).

### 3. Read-only issue / discussion planning (`/antigravity-plan`)

The `/antigravity-plan` command reads a GitHub issue or discussion through the GitHub MCP server, explores the repository read-only (following `AGENTS.md` and `.agents/skills`), and returns a Markdown `## Analysis` and `## Development plan` in the `response` output. It never writes to GitHub: for this command the GitHub MCP tools are filtered through a read-only allowlist (`issue_read`, `list_issues`, `search_issues`, `pull_request_read`, `list_pull_requests`, `search_pull_requests`, `get_commit`, `list_commits`, `get_file_contents`, `search_code`, `get_discussion`, `get_discussion_comments`), so any other tool passed through `additional_gh_tools` is dropped. In addition, the GitHub MCP server is started in read-only mode (`GITHUB_READ_ONLY=1`), so the server itself refuses write tools. Local tools are limited to `view_file` and `find_file` (no shell). The calling workflow is responsible for posting the result.

| Input | Description |
| --- | --- |
| `item_type` | `issue` or `discussion` (required by `/antigravity-plan`) |
| `item_number` | Positive integer: number of the issue or discussion (required by `/antigravity-plan`) |

The item body is never injected into the prompt; the agent reads it itself and treats it as untrusted data. For `/antigravity-plan` with `item_type: discussion`, `get_discussion` and `get_discussion_comments` are enabled. Whenever one of these discussion tools is enabled (by this command, or explicitly through `additional_gh_tools` for other prompts), the server is started with `GITHUB_TOOLSETS=default,discussions`, because the `discussions` toolset is not part of the server defaults.

> [!WARNING]
> `additional_context` (or `goal-prompt` as fallback) is interpolated into the agent's **system instructions**. Never pass untrusted text in it (issue/discussion/PR bodies, comments, titles from external contributors); pass only maintainer-controlled instructions.

```yaml
      - name: 'Plan issue'
        uses: 'vincentpichon/run-agy-sdk@ai-plan'
        id: 'agy_plan'
        with:
          api-key: '${{ secrets.ANTIGRAVITY_API_KEY }}'
          prompt: '/antigravity-plan'
          item_type: 'issue'
          item_number: '${{ github.event.issue.number }}'
      # Post ${{ steps.agy_plan.outputs.response }} from a later step (e.g. with gh / actions/github-script)
```

`additional_gh_tools` accepts a comma-separated list of extra GitHub MCP tools; empty entries are ignored.

## 🤝 Acknowledgments

This project is inspired by [run-gemini-cli](https://github.com/google-github-actions/run-gemini-cli).

## 📄 License

This project is licensed under the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for the full license text.

## 📝 NOTE

This is not an officially supported Google product.