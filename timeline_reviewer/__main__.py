"""Small command line interface. Default operation requires only Python."""
from pathlib import Path
import argparse
import json
import os
import sys
import webbrowser

from . import __version__


def parser():
    root = argparse.ArgumentParser(description='Review rendered video and its timeline locally. No project editing or uploads.')
    root.add_argument('--version', action='version', version=__version__)
    commands = root.add_subparsers(dest='command', required=True)
    for name, help_text in [('demo', 'Open the included synthetic example'), ('serve', 'Serve an existing review bundle')]:
        item = commands.add_parser(name, help=help_text)
        if name == 'serve':
            item.add_argument('bundle', type=Path)
            item.add_argument('--editable-project', type=Path,
                              help='Optional local Tesseract document for limited timeline edits')
            item.add_argument('--media-root', type=Path, action='append', default=[],
                              help='Permit draft preview of source video within this trusted local folder; repeat as needed')
            item.add_argument('--tesseract', help='Path to a separately installed Tesseract CLI')
            item.add_argument('--capcut-project', type=Path,
                              help='Opt in to sync from this exact saved local CapCut project folder')
            item.add_argument('--capcut-timeline',
                              help='Exact nested CapCut timeline name to inspect')
            item.add_argument('--sync-root', type=Path,
                              help='Separate local folder for retained sync versions and last successful comparison')
        item.add_argument('--port', type=int, default=8765)
        item.add_argument('--open', action='store_true', help='Open the local URL in your default browser')
    item = commands.add_parser('validate', help='Validate a bundle and its referenced files')
    item.add_argument('bundle', type=Path)
    item = commands.add_parser('prepare', help='Make a new review bundle from a movie; requires FFmpeg')
    item.add_argument('--video', type=Path, required=True)
    item.add_argument('--output', type=Path, required=True)
    item.add_argument('--timeline', type=Path, help='Optional version 1 timeline metadata')
    item.add_argument('--title')
    item = commands.add_parser('inspect-capcut', help='Read one saved CapCut timeline without Tesseract')
    item.add_argument('project', type=Path)
    item.add_argument('--timeline', required=True)
    item.add_argument('--report', type=Path)
    item = commands.add_parser('import-capcut', help='Optional experimental one way transfer into a new Tesseract project')
    item.add_argument('project', type=Path)
    item.add_argument('--timeline', required=True)
    item.add_argument('--output', type=Path, required=True)
    item.add_argument('--tesseract', help='Path to an independently installed Tesseract CLI')
    item.add_argument('--allow-lossy', action='store_true', help='Explicitly accept unsupported feature omissions listed in the report')
    item.add_argument('--render', action='store_true', help='Also request a native draft render; audio and export limitations apply')
    item = commands.add_parser('launch', help='Start or reuse the configured private local Madison server')
    item.add_argument('--no-open', action='store_true')
    commands.add_parser('stop', help='Stop the owned local Madison server when restart safe')
    item = commands.add_parser('configure', help='Save the private local project selection')
    item.add_argument('--mode', choices=('demo', 'serve'), default='demo')
    item.add_argument('--port', type=int, default=8464)
    item.add_argument('--bundle', type=Path)
    item.add_argument('--editable-project', type=Path)
    item.add_argument('--media-root', type=Path, action='append', default=[])
    item.add_argument('--tesseract', type=Path)
    item.add_argument('--capcut-project', type=Path)
    item.add_argument('--capcut-timeline')
    item.add_argument('--sync-root', type=Path)
    item.add_argument('--project-label')
    item = commands.add_parser('rollback', help='Select the previously recorded application and config')
    item.add_argument('--launch', action='store_true')
    item = commands.add_parser('select-app', help='Select a Madison application folder and record rollback')
    item.add_argument('app_root', type=Path)
    item.add_argument('--build-id')
    item = commands.add_parser('_run', help=argparse.SUPPRESS)
    item.add_argument('mode', choices=('demo', 'serve'))
    item.add_argument('--port', type=int, required=True)
    item.add_argument('--state-dir', type=Path, required=True)
    item.add_argument('--instance-id', required=True)
    item.add_argument('--start-identity', required=True)
    item.add_argument('--build-id', required=True)
    item.add_argument('--config-fingerprint', required=True)
    item.add_argument('--shutdown-token', required=True)
    item.add_argument('--app-root', type=Path, required=True)
    item.add_argument('--bundle', type=Path)
    item.add_argument('--editable-project', type=Path)
    item.add_argument('--media-root', type=Path, action='append', default=[])
    item.add_argument('--tesseract', type=Path)
    item.add_argument('--capcut-project', type=Path)
    item.add_argument('--capcut-timeline')
    item.add_argument('--sync-root', type=Path)
    item.add_argument('--project-label')
    return root


def _serve(args, *, runtime_identity=None, shutdown_token=None, open_browser=False):
    from .server import make_server
    command = args.mode if args.command == '_run' else args.command
    bundle = Path(__file__).resolve().parent / 'demo' if command == 'demo' else args.bundle
    if command == 'serve' and bundle is None:
        raise ValueError('Serve mode requires a review bundle.')
    edit_session = None
    if command == 'serve' and args.editable_project:
        from .editing import EditSession
        edit_session = EditSession(bundle, args.editable_project,
                                   cli=args.tesseract, media_roots=args.media_root or None)
    elif command == 'serve' and (args.media_root or args.tesseract):
        if args.media_root or not (args.capcut_project and args.capcut_timeline and args.sync_root):
            raise ValueError('--media-root requires --editable-project; --tesseract requires --editable-project or all CapCut sync options')
    capcut_options = (args.capcut_project, args.capcut_timeline, args.sync_root) if command == 'serve' else ()
    if capcut_options and any(value is not None for value in capcut_options) and not all(value is not None for value in capcut_options):
        raise ValueError('--capcut-project, --capcut-timeline, and --sync-root must be supplied together')
    capcut_sync = None
    if capcut_options and all(value is not None for value in capcut_options):
        from .sync import CapCutSync
        capcut_sync = CapCutSync(bundle, args.capcut_project, args.capcut_timeline,
                                 args.sync_root, cli=args.tesseract)
    with make_server(bundle, args.port, edit_session=edit_session, capcut_sync=capcut_sync,
                     runtime_identity=runtime_identity, shutdown_token=shutdown_token) as server:
        address = f'http://127.0.0.1:{server.server_port}/'
        mode = 'Limited local editing' if edit_session else 'Read only'
        if capcut_sync:
            mode += ' with optional CapCut sync'
        print(f'Madison: {address}\n{mode}. Press Ctrl+C to stop.', flush=True)
        if open_browser:
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in ('demo', 'serve'):
            _serve(args, open_browser=args.open)
        elif args.command == '_run':
            from .identity import capture_runtime_identity
            identity = capture_runtime_identity(args.project_label, args.config_fingerprint,
                                                package_root=Path(__file__).resolve().parent,
                                                instance_id=args.instance_id,
                                                start_identity=args.start_identity,
                                                pid=os.getpid())
            if identity['buildId'] != args.build_id or identity['startIdentity'] != args.start_identity:
                raise RuntimeError('Madison source changed while the server was starting. Launch again.')
            _serve(args, runtime_identity=identity, shutdown_token=args.shutdown_token)
        elif args.command == 'configure':
            from .launcher import configure
            config = configure(mode=args.mode, port=args.port, bundle=args.bundle,
                               editable_project=args.editable_project, media_roots=args.media_root,
                               tesseract=args.tesseract, capcut_project=args.capcut_project,
                               capcut_timeline=args.capcut_timeline, sync_root=args.sync_root,
                               project_label=args.project_label)
            print(f"Madison configuration saved for {config.get('projectLabel', config['mode'])}.")
        elif args.command == 'launch':
            from .launcher import launch, selected_app_root
            result = launch(open_browser=not args.no_open, app_root=selected_app_root())
            print(f'Madison {result.action}: {result.url}')
        elif args.command == 'stop':
            from .launcher import stop
            stop()
            print('Madison stopped.')
        elif args.command == 'rollback':
            from .launcher import launch, rollback
            selection = rollback()
            print(f"Madison launcher restored: {selection['appRoot']}")
            if args.launch:
                result = launch(app_root=selection['appRoot'])
                print(f'Madison {result.action}: {result.url}')
        elif args.command == 'select-app':
            from .launcher import select_app
            selection = select_app(app_root=args.app_root, build_id=args.build_id)
            print(f"Madison application selected: {selection['appRoot']}")
        elif args.command == 'validate':
            from .manifest import load_manifest, asset_files
            data = load_manifest(args.bundle / 'data.json')
            files = asset_files(args.bundle, data)
            print(f'Valid bundle: {len(data["tracks"])} lanes, {sum(len(t["clips"]) for t in data["tracks"])} clips, {len(files)} media files.')
        elif args.command == 'prepare':
            from .prepare import prepare_bundle
            print(json.dumps(prepare_bundle(args.video, args.output, args.timeline, args.title), indent=2))
        elif args.command == 'inspect-capcut':
            from .capcut import inspect_project
            report = inspect_project(args.project, args.timeline)
            rendered = json.dumps(report, indent=2, ensure_ascii=False)
            if args.report:
                with args.report.open('x', encoding='utf-8') as file:
                    file.write(rendered)
                print(f'Inspection saved: {args.report}')
            else:
                print(rendered)
        elif args.command == 'import-capcut':
            from .tesseract import import_project
            print(json.dumps(import_project(args.project, args.timeline, args.output, args.tesseract, args.allow_lossy, args.render), indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
