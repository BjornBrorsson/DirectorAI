# Board Configuration

This configuration file defines board behavior, humans, and AI agents for this Tickets board.
AI agents and humans can reference or edit this file.

## Settings
- **Board Name**: DirectorCut-AI Board
- **Columns Order**: Backlog, Ongoing, Assistance Required, Blocked, Completed
- **Auto Update Status In File**: true
- **Default Agent**: devin-cli

## Assignees

### Humans
- **Björn**
  - ID: `bjorn`
  - Role: Project Lead & Reviewer
  - Type: human

### Agents

- **Devin CLI**
  - ID: `devin-cli`
  - Type: cli
  - Command: `devin -p "Please review and work on ticket {ticket_path}: {ticket_title}"`
  - WorkingDir: `{workspace_root}`

- **Claude Code**
  - ID: `claude-code`
  - Type: cli
  - Command: `claude -p "Review and implement ticket {ticket_path}"`
  - WorkingDir: `{workspace_root}`

- **IDE Chat (Devin / Copilot)**
  - ID: `ide-chat`
  - Type: vscode-command
  - Command: `workbench.action.chat.open`
  - Prompt: `Please review and work on ticket '{ticket_title}' located at: {ticket_path}\n\nTicket Summary:\n{ticket_content}`
