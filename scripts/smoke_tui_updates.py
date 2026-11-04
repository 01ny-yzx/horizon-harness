"""Offline release validation and updater behaviour."""
import asyncio
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
import httpx

from tui.updates import UpdateManager, Installation, release_version

class Updates(unittest.IsolatedAsyncioTestCase):
    def test_only_numeric_stable_releases_are_accepted(self):
        self.assertEqual(release_version('v1.0.12'), (1,0,12))
        for value in ['1.0.3-beta', '1.2', 'v1.0.3;echo hi', '01.2.3', None]:
            self.assertIsNone(release_version(value))

    async def test_newer_version_skip_persistence_and_manual_check(self):
        with tempfile.TemporaryDirectory() as temp:
            seen=[]
            def reply(request):
                seen.append(str(request.url))
                return httpx.Response(200,json={'tag_name':'v1.0.3','draft':False,'prerelease':False})
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
                manager=UpdateManager(Path(temp)/'updates.json', current='1.0.1', installation=Installation('npm'), http=http)
                self.assertEqual(await manager.check(), '1.0.3')
                manager.skip('1.0.3')
                self.assertFalse(manager.should_notify('1.0.3'))
                self.assertEqual(await manager.check(manual=True), '1.0.3')
                restored=UpdateManager(manager.path,current='1.0.1',installation=Installation('npm'),http=http)
                self.assertFalse(restored.should_notify('1.0.3'))
                self.assertEqual(len(seen),2)

    async def test_old_draft_and_prerelease_never_offer_update(self):
        for version,draft,pre in [('v1.0.0',False,False),('v1.0.1',False,False),('v1.0.3',True,False),('v1.0.3',False,True)]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'tag_name':version,'draft':draft,'prerelease':pre}))) as http:
                manager=UpdateManager(Path('/unused'),current='1.0.1',installation=Installation('brew'),http=http)
                self.assertIsNone(await manager.check())

    async def test_background_failure_is_silent_manual_failure_is_visible(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(429))) as http:
            manager=UpdateManager(Path('/unused'),installation=Installation('npm'),http=http)
            self.assertIsNone(await manager.check())
            with self.assertRaises(Exception): await manager.check(manual=True)

    async def test_missing_optional_proxy_dependency_does_not_crash_startup(self):
        manager=UpdateManager(Path('/unused'),installation=Installation('npm'))
        with patch('tui.updates.httpx.AsyncClient',side_effect=ImportError('SOCKS support missing')):
            self.assertIsNone(await manager.check())
            with self.assertRaises(RuntimeError):
                await manager.check(manual=True)

    async def test_source_does_not_contact_network(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:self.fail('unexpected network'))) as http:
            manager=UpdateManager(Path('/unused'),installation=Installation('source'),http=http)
            self.assertIsNone(await manager.check())

    def test_handoff_version_is_validated_and_written_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            request=Path(temp)/'request.json'
            manager=UpdateManager(Path(temp)/'updates.json',installation=Installation('npm',request=str(request)))
            manager.handoff('1.0.3')
            self.assertEqual(json.loads(request.read_text()),{'version':'1.0.3'})
            with self.assertRaises(ValueError): manager.handoff('1.0.3;bad')

class UpdateUI(unittest.IsolatedAsyncioTestCase):
    async def test_actions_have_no_inner_frame_and_all_rows_are_visible(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_ui import FakeClient
        for method, status in [('source',200),('npm',200),('npm',429)]:
            with self.subTest(method=method,status=status), tempfile.TemporaryDirectory() as temp:
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(status,json={'tag_name':'v1.0.3'}))) as http:
                    manager=UpdateManager(Path(temp)/'updates.json',current='1.0.1',installation=Installation(method),http=http)
                    app=HorizonApp(FakeClient(),updater=manager)
                    async with app.run_test(size=(100,30)) as pilot:
                        await app.action_update()
                        await pilot.pause(.1)
                        options=app.screen.query_one('#update-actions')
                        for edge in ('top','right','bottom','left'):
                            self.assertIn(getattr(options.styles,'border_'+edge)[0], ('','none'))
                        self.assertEqual(options.content_size.height,len(app.screen.actions))
                        self.assertEqual(options.region.x,app.screen.query_one('#update-status').region.x)

    async def test_startup_notice_manual_dialog_skip_and_same_version_dedup(self):
        from tui.app import HorizonApp
        from tui.update_dialog import UpdateDialog
        from scripts.smoke_tui_ui import FakeClient
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            def reply(request):
                calls.append(1)
                return httpx.Response(200,json={'tag_name':'v1.0.3'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
                manager=UpdateManager(Path(temp)/'updates.json',current='1.0.1',installation=Installation('npm'),http=http)
                app=HorizonApp(FakeClient(),updater=manager)
                async with app.run_test(size=(100,30)) as pilot:
                    await pilot.pause(.1)
                    self.assertEqual(len(calls),1)
                    self.assertIn('Update available',app.query_one('#version-hint').render().plain)
                    await pilot.resize_terminal(45,24)
                    await pilot.pause(.05)
                    self.assertLessEqual(app.query_one('#version-hint').region.right,app.query_one('#composer-footer').content_region.right)
                    await pilot.resize_terminal(100,30)
                    await app.action_update()
                    await pilot.pause(.1)
                    self.assertIsInstance(app.screen,UpdateDialog)
                    self.assertEqual(app.screen.version,'1.0.3')
                    await pilot.press('down','enter')
                    await pilot.pause(.1)
                    self.assertFalse(manager.should_notify('1.0.3'))
                    self.assertNotIn('Update available',app.query_one('#version-hint').render().plain)
                    await app.action_update()
                    await pilot.pause(.1)
                    self.assertEqual(app.screen.version,'1.0.3')
                    await pilot.press('escape')
                    self.assertIsNone(app.update_requested)

    async def test_failure_dialog_retries_and_install_requires_confirmation(self):
        from tui.app import HorizonApp
        from tui.update_dialog import UpdateDialog
        from scripts.smoke_tui_ui import FakeClient
        with tempfile.TemporaryDirectory() as temp:
            responses=[httpx.Response(429),httpx.Response(429),httpx.Response(200,json={'tag_name':'v1.0.3'})]
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:responses.pop(0))) as http:
                manager=UpdateManager(Path(temp)/'updates.json',current='1.0.1',installation=Installation('npm'),http=http)
                app=HorizonApp(FakeClient(),updater=manager)
                async with app.run_test(size=(80,24)) as pilot:
                    await pilot.pause(.1)
                    await app._run_command('update')
                    await pilot.pause(.1)
                    self.assertEqual(app.screen.actions[0][0],'retry')
                    await pilot.press('enter')
                    await pilot.pause(.1)
                    self.assertEqual(app.screen.actions[0][0],'install')
                    self.assertIsNone(app.update_requested)
                    app.busy=True
                    await pilot.press('enter')
                    await pilot.pause(.1)
                    self.assertIsNone(app.update_requested)

    async def test_confirm_installs_only_by_returning_a_shutdown_request(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_ui import FakeClient
        with tempfile.TemporaryDirectory() as temp:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'tag_name':'v1.0.3'}))) as http:
                manager=UpdateManager(Path(temp)/'updates.json',current='1.0.1',installation=Installation('npm'),http=http)
                app=HorizonApp(FakeClient(),updater=manager)
                async with app.run_test(size=(80,24)) as pilot:
                    await pilot.pause(.05)
                    await app.action_update()
                    await pilot.pause(.05)
                    self.assertIsNone(app.update_requested)
                    await pilot.press('enter')
                self.assertEqual(app.update_requested,'1.0.3')

    async def test_busy_task_prevents_opening_installer(self):
        from tui.app import HorizonApp
        from tui.update_dialog import UpdateDialog
        from scripts.smoke_tui_ui import FakeClient
        manager=UpdateManager(Path('/unused'),installation=Installation('source'))
        app=HorizonApp(FakeClient(),updater=manager)
        async with app.run_test(size=(80,24)) as pilot:
            app.busy=True
            await app.action_update()
            self.assertNotIsInstance(app.screen,UpdateDialog)

class UpdateExecution(unittest.TestCase):
    def test_npm_handoff_occurs_without_starting_a_package_manager_in_native_process(self):
        from tui.updates import perform_update
        with tempfile.TemporaryDirectory() as temp:
            request=Path(temp)/'request.json'
            manager=UpdateManager(Path(temp)/'settings.json',installation=Installation('npm',request=str(request)))
            with patch('core.external_process.run_external') as run:
                self.assertEqual(perform_update(manager,'1.0.3',[]),85)
                run.assert_not_called()
            self.assertEqual(json.loads(request.read_text())['version'],'1.0.3')

    def test_brew_progress_verification_and_restart_are_ordered(self):
        from types import SimpleNamespace
        from tui.updates import perform_update
        manager=UpdateManager(Path('/unused'),installation=Installation('brew',command='/usr/bin/brew'))
        results=[None,None,SimpleNamespace(stdout='/brew/opt/horizon\n'),SimpleNamespace(stdout='Horizon 1.0.3\n')]
        with patch('core.external_process.run_external',side_effect=results) as run, patch('os.execve') as restart:
            perform_update(manager,'1.0.3',['--project','hello'])
            self.assertEqual(run.call_args_list[0].args[0],['/usr/bin/brew','update'])
            self.assertEqual(run.call_args_list[1].args[0],['/usr/bin/brew','upgrade','01ny-yzx/tap/horizon'])
            self.assertEqual(restart.call_args.args[1],[str(Path('/brew/opt/horizon/bin/horizon')),'--project','hello'])
            self.assertEqual(restart.call_args.args[2]['PYINSTALLER_RESET_ENVIRONMENT'],'1')

    def test_failed_or_unsynced_update_does_not_restart(self):
        from types import SimpleNamespace
        from tui.updates import perform_update
        manager=UpdateManager(Path('/unused'),installation=Installation('brew',command='brew'))
        for results in [[RuntimeError('offline')],[None,None,SimpleNamespace(stdout='/brew/opt/horizon'),SimpleNamespace(stdout='Horizon 1.0.1')]]:
            with patch('core.external_process.run_external',side_effect=results),patch('os.execve') as restart:
                self.assertEqual(perform_update(manager,'1.0.3',[]),1)
                restart.assert_not_called()

    def test_curl_uses_pinned_release_and_preserves_install_location(self):
        from types import SimpleNamespace
        from tui.updates import perform_update
        manager=UpdateManager(Path('/unused'),installation=Installation('curl',root='/custom/data',binary='/custom/bin/horizon'))
        with patch('core.external_process.run_external',side_effect=[None,None,SimpleNamespace(stdout='Horizon 1.0.3')]) as run,patch('os.execve') as restart:
            perform_update(manager,'1.0.3',[])
            self.assertIn('https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.3/install.sh',run.call_args_list[0].args[0])
            self.assertEqual(run.call_args_list[1].kwargs['env']['HORIZON_INSTALL_ROOT'],'/custom/data')
            self.assertEqual(run.call_args_list[1].kwargs['env']['HORIZON_BIN_DIR'],str(Path('/custom/bin/horizon').parent))
            self.assertEqual(restart.call_args.args[0],'/custom/bin/horizon')

class InstallationDetection(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'macOS curl installations')
    def test_custom_curl_link_is_detected_from_durable_receipt(self):
        import sys
        from tui.updates import detect_installation
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'runtime'; exe=root/'versions/1.0.1-macos-arm64/horizon'
            exe.parent.mkdir(parents=True);exe.touch()
            (exe.parent/'.horizon-managed').touch()
            binary=Path(temp)/'custom bin/horizon';binary.parent.mkdir();binary.symlink_to(exe)
            (exe.parent/'.horizon-bin').write_text(str(binary)+'\n')
            with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(exe)),patch.object(sys,'platform','darwin'),patch.dict('os.environ',{'HORIZON_BIN_DIR':'/not-the-installation','HORIZON_UPDATE_REQUEST':'','HORIZON_NPM_ROOT':''}):
                found=detect_installation()
                self.assertEqual(found.method,'curl')
                self.assertEqual(found.binary,str(binary))
                binary.unlink();binary.symlink_to(Path(temp)/'another-executable')
                self.assertEqual(detect_installation().method,'unknown')

class UpdateShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_update_request_returns_only_after_ui_client_and_service_close(self):
        from types import SimpleNamespace
        import tui.__main__ as entry
        events=[]
        class Service:
            url='http://127.0.0.1:1';api_key='test';directory='/work'
            async def start(self): events.append('start')
            async def stop(self):
                await asyncio.sleep(0)
                events.append('service stopped')
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): events.append('client closed')
        class App:
            update_requested='1.0.3'
            def __init__(self,*args,**kwargs): pass
            async def run_async(self): events.append('ui closed')
        with tempfile.TemporaryDirectory() as temp:
            preferences=SimpleNamespace(language='en',path=Path(temp)/'preferences.json')
            args=SimpleNamespace(url=None,user=None,project=None,directory=None)
            with patch.object(entry,'Preferences',return_value=preferences),patch.object(entry,'LocalService',return_value=Service()),patch.object(entry,'HorizonClient',return_value=Client()),patch.object(entry,'HorizonApp',App):
                request=await entry.run(args)
            self.assertEqual(events,['start','ui closed','client closed','service stopped'])
            self.assertEqual(request[1],'1.0.3')
