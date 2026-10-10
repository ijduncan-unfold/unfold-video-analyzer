# Video Analyzer for DaVinci Resolve Studio

A native Workflow Integration window for **Resolve Studio 20.1+**, using the local Video Analyzer companion. Windows x64 comes first; the Mac path targets native Apple Silicon. The free edition does not support this Workflow Integration window.

Load selected original Media Pool clips, choose **Shots and tags** or **Full analysis**, and click **Analyze**. Search progressive shot results or dialogue, select a source clip in the Media Pool, copy file-relative in/out times, export SRT, or add source markers to verified constant-rate originals. **Open full analyzer** provides the complete metadata, export, and visual match-cut workspace.

Use **Install Resolve integration** from the companion's Windows Start menu/tray or Mac menu bar. The standalone public ZIP also includes **Install Resolve on Windows.cmd** and **Install Resolve on Mac.command**. Install with Resolve closed, start the companion, then open **Workspace → Workflow Integrations → Video Analyzer**. The integration connects automatically; there is no pairing code to copy. Windows uses `http://127.0.0.1:8000`; Mac uses the trusted local endpoint `https://localhost:8443`. If the companion is closed, the window waits and reconnects when it starts.

The installer copies `WorkflowIntegration.node` from your own matching Blackmagic SDK. This vendor module is never redistributed in the public ZIP or companion. Windows requires the x64 module; Mac requires a bridge containing arm64 and refuses Intel-only modules. Resolve supplies Electron, so the plugin needs no separately installed Node.js runtime.

Original media is referenced and preserved. Saved analysis stays in the companion; plugin clip/search preferences persist per Resolve project. The native main process discovers the running app through a private per-user file and authenticates behind the scenes. Credentials never enter the interface or saved preferences. Analysis is explicit and billable through the configured Gemini key.

Times are measured from the beginning of the original file. **Select clip** selects a Media Pool item; it does not seek the source viewer to the displayed range. Marker writes preserve existing markers and attempt careful rollback of their own additions if a batch fails. This preview has no documented native marker transaction/undo API, and AI timing remains approximate.

The implementation has automated backend, host, panel, and packaging checks. Windows native testing in Resolve Studio 21.0.1 verified the Workspace menu, searches, selection, subtitles, guarded source markers, persistence, project switching, and shutdown on an isolated 24 fps fixture. A separate native acceptance round verified automatic connection, panel close/reopen, waiting while the companion is stopped, and automatic reconnection after restart. Mac SDK compatibility and native Mac operations remain pending. See [installation, paths, limits, and native acceptance](../../docs/RESOLVE_HANDOFF.md).

Run the portable integration tests without Resolve or its SDK:

```sh
node --test integrations/resolve/tests/*.test.cjs
```
