# Acceptance criteria — Build workspace

Interaction-level GIVEN/WHEN/THEN for the local coding-engine workspace. Browser QA must not
start a real Codex or Claude Code run unless the operator explicitly authorizes paid CLI use.

---

### BUILDER-NAV
- GIVEN I am anywhere in Open Net Worth
- WHEN I choose **Build** from the configuration navigation, mobile More sheet, or command palette
- THEN `#/build` opens and the page title is “Build Open Net Worth” (or the configured app name).

### BUILDER-READINESS
- GIVEN the Build workspace is open
- WHEN engine readiness finishes loading
- THEN Codex and Claude Code each show an honest Ready or Not installed state, the fixed workspace
  path is visible, and an unavailable engine cannot be selected for a run.

### BUILDER-FIRST-RUN
- GIVEN neither Codex nor Claude Code is installed
- WHEN engine readiness finishes loading
- THEN the page explains how to install and sign in to either local engine, states that Open Net
  Worth does not receive the engine credentials, and offers **Check again**.

### BUILDER-PLAIN-LANGUAGE
- GIVEN I do not know how the codebase works
- WHEN I choose a starter such as “Add something new”
- THEN a plain-language example is inserted into the change request and remains editable.

### BUILDER-AUTHORITY
- GIVEN I am preparing a change
- WHEN I switch between Plan and Build
- THEN Plan says it can only read and return a plan, while Build says it can edit the checkout and
  run local commands; the page also says Builder provides no release actions.

### BUILDER-START
- GIVEN the fixed workspace and selected engine are ready and my request is non-empty
- WHEN I choose “Plan this change” or “Build this change”
- THEN exactly one run starts with the selected engine and authority, the composer is disabled, an
  activity surface appears, and a Stop control is available.

### BUILDER-CONFIRM
- GIVEN I selected Build authority and entered a request
- WHEN I choose **Build this change**
- THEN a confirmation repeats the selected engine and fixed workspace, states that the engine is
  instructed not to commit, push, open a pull request, or deploy, says Builder provides no release
  actions, and waits for **Start building** before starting the run.

### BUILDER-PROGRESS
- GIVEN a run is active
- WHEN the coding engine emits status, assistant, tool, output, or error events
- THEN user-facing status and assistant updates appear in order, tool and command output is available
  behind **Show technical activity**, and raw JSON is not the primary experience.

### BUILDER-STOP
- GIVEN a run or its progress poll is still active
- WHEN I choose Stop
- THEN the in-flight poll is cancelled, the coding process is stopped, and the run reads “Stopped.”

### BUILDER-RESULT
- GIVEN a run succeeds, fails, or is stopped
- WHEN it reaches a terminal state
- THEN the page presents the plan/change as ready to review or explains that the run needs attention,
  shows the final activity or error, and shows the checkout's current Git status labelled “Workspace
  changes,” without claiming every dirty file came from that run.

### BUILDER-RESPONSIVE
- GIVEN I open Build on a phone or tablet
- WHEN the responsive layout collapses to one column
- THEN the change request and active work appear before engine and workspace diagnostics, and the
  mobile More sheet explains that Build customizes Open Net Worth with a local coding engine.

### BUILDER-ERROR
- GIVEN the readiness or run API cannot be reached
- WHEN the request fails
- THEN a plain-language error is shown; readiness failures offer Retry and do not render a blank page.
