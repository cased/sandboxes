# Hopx API Reference

This document contains the full API reference for implementing the Hopx provider.

## Authentication

Hopx uses **API keys** for request authentication. Keys follow the format `hopx_live_<keyId>.<secret>` and are obtained from the dashboard.

**Supported methods:**
- `X-API-Key` header (recommended)
- `Authorization: Bearer` header
- Environment variable (`HOPX_API_KEY`)

Keys should never be hardcoded; use environment variables or secrets managers instead.

## API Structure

The platform provides two main API sections:

**Lifecycle API** (`/v1/sandboxes`, `/v1/templates`): Manage sandbox creation, deletion, listing, and state transitions (start, stop, pause, resume).

**VM Agent API** (`https://{sandbox_id}.hopx.dev`): Interact with running sandboxes for code execution, file operations, and system management.

## Core Endpoints

### Sandbox Management
- `POST /v1/sandboxes` - Create sandbox from template
- `GET /v1/sandboxes` - List all sandboxes (with filtering)
- `GET /v1/sandboxes/{id}` - Get sandbox details
- `DELETE /v1/sandboxes/{id}` - Delete sandbox
- `POST /v1/sandboxes/{id}/{action}` - Control operations (start, stop, pause, resume)

### Template Operations
- `GET /v1/templates` - List templates
- `GET /v1/templates/{id}` - Get template details
- `POST /v1/templates/build` - Create custom template
- `DELETE /v1/templates/{id}` - Delete template

### Code Execution
- `POST {sandbox_host}/execute` - Execute code
- `POST {sandbox_host}/execute/rich` - Execute with rich outputs (plots, DataFrames)
- `POST {sandbox_host}/commands/run` - Run shell commands
- `GET {sandbox_host}/execute/processes` - List processes
- `POST {sandbox_host}/execute/kill/{id}` - Terminate process

### File Operations
- `GET /files/read` - Read file content
- `POST /files/write` - Create/update file
- `GET /files/list` - List directory contents
- `GET /files/download` - Download file
- `POST /files/upload` - Upload file (multipart/form-data)

### Additional Features
- **Environment Variables**: GET, PUT, PATCH, DELETE operations on `/env`
- **Metrics**: `GET /metrics/snapshot` and health checks
- **Cache Management**: Get stats and clear cache
- **Desktop Automation**: VNC access, screenshots, mouse/keyboard control
- **WebSocket Support**: Real-time streaming for code execution, terminal, and file watching

## Request/Response Format

**Headers:**
```
Content-Type: application/json
X-API-Key: your_api_key_here
```

**Success responses** return JSON with resource data; **error responses** include `error`, `code`, and optional `message` fields.

## Supported Languages

- Python
- JavaScript/Node.js
- Bash
- Go

## Rate Limiting

Rate limits vary by organization. Limits are communicated via headers:
- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`

Template building is limited to 10 builds/hour and 50 builds/day by default.

## Special Features

**Memory Snapshots**: Templates use memory snapshots for sub-100ms boot times.

**Sandbox States**: running, stopped, paused, creating.

**Rich Output Support**: Captures plots, DataFrames, and other formatted outputs.

**Real-time Streaming**: WebSocket endpoints enable live code execution and file system monitoring.

**Environment Isolation**: Sandboxes support custom resource allocation and internet access control.

## Implementation Notes for Provider

### Base URL
The main API base URL should be configurable, likely: `https://api.hopx.dev`

### Two-Level API Access
1. **Control Plane**: `https://api.hopx.dev/v1/*` - Lifecycle management
2. **Data Plane**: `https://{sandbox_id}.hopx.dev/*` - Code execution and file operations

### Key Differences from Other Providers
- Uses HTTP REST API (like Cloudflare provider)
- Requires template selection for sandbox creation
- Supports multiple sandbox states (running, stopped, paused)
- Has separate endpoints for lifecycle vs execution
- Supports rich output formats (plots, DataFrames)

### Recommended Implementation Approach
1. Use `aiohttp` for async HTTP requests (consistent with Cloudflare provider)
2. Store base URL and API key in config
3. Track sandbox state transitions (creating → running → stopped)
4. Implement streaming execution using WebSocket or SSE
5. Support template-based creation with default template fallback
