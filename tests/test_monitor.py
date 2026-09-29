import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ollama_monitor.api import make_server
from ollama_monitor.collector import Monitor
from ollama_monitor.context import LogReader, get_context_metrics, _extract_n_decoded
from ollama_monitor.history import History
from ollama_monitor.io import numeric
from ollama_monitor.memory import parse_vm_stat, parse_swap, get_memory_pressure_metrics
from ollama_monitor.mcp import dispatch, URI
from ollama_monitor.ollama import get_loaded_models, get_ollama_status
from ollama_monitor.processes import parse_processes, get_ollama_memory_metrics, runner_processes

VM = '''Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free: 400.
Anonymous pages: 100.
Pages wired down: 20.
Pages occupied by compressor: 10.
Pages stored in compressor: 1000.
File-backed pages: 100.
'''
PROCESS_TABLE = '''1 0 0.0 0.0 100 01:00 launchd /sbin/launchd
10 1 1.0 2.0 200 01:00 Ollama /Applications/Ollama.app/Contents/MacOS/Ollama
11 10 2.0 3.0 300 00:59 ollama /opt/bin/ollama serve
12 11 4.0 4.0 400 00:58 wrapper wrapper
13 12 5.0 5.0 500 00:57 llama-server llama-server --port 40001
14 11 6.0 6.0 600 00:56 Python python -m mlx.server --port=40002
20 1 3.0 3.0 9999 00:55 llama-server llama-server --model /ollama/model --port 11435
21 1 0.0 0.0 100 00:54 Python python ollama-watch.py
30 31 0.0 0.0 100 00:54 odd odd
31 30 0.0 0.0 100 00:54 odd odd
'''
MODELS = [dict(name='model-a', digest='abc', context_length=4096)]
RUNNER = dict(pid=13, command='ollama runner --port 40001', exe='ollama')


def snapshot(timestamp=None):
    return dict(timestamp=time.time() if timestamp is None else timestamp,
                system_memory=dict(used_bytes=100, total_bytes=200, used_percent=50, swap_used_bytes=0),
                ollama_memory=dict(used_bytes=20, used_percent_of_system_ram=10),
                memory_pressure=dict(pressure_percent=30, free_percent=70),
                context=dict(used_tokens=None, max_tokens=4096, used_percent=None, source='unavailable',
                             runner_pid=None, slot=None, model_name='model-a'))


class MemoryTests(unittest.TestCase):
    def test_compressed_physical_pages_not_logical_or_free(self):
        result = parse_vm_stat(VM, 1000 * 16384)
        self.assertEqual(result['used_bytes'], 130 * 16384)
        self.assertEqual(result['used_percent'], 13)
        self.assertEqual(result['compressed_bytes'], 10 * 16384)

    def test_missing_fields_and_invalid_data_are_null(self):
        self.assertIsNone(parse_vm_stat(VM.replace('Anonymous pages', 'Unknown'), 10000)['used_bytes'])
        self.assertIsNone(parse_vm_stat(VM, 1)['used_bytes'])
        self.assertIsNone(parse_vm_stat(None, None)['used_percent'])

    def test_swap_units(self):
        self.assertEqual(parse_swap('total = 10G used = 1.5G free = 8.5G'), 1610612736)
        self.assertEqual(parse_swap('used = 0.00M'), 0)
        self.assertIsNone(parse_swap(None))

    def test_pressure_normalization_and_null(self):
        for output, free, pressure in [('System-wide memory free percentage: 34%', 34, 66),
                                       ('System-wide memory free percentage: 100%', 100, 0),
                                       ('System-wide memory free percentage: 101%', None, None),
                                       (None, None, None)]:
            with self.subTest(output=output), patch('ollama_monitor.memory.run', return_value=output):
                result = get_memory_pressure_metrics()
                self.assertEqual((result['free_percent'], result['pressure_percent']), (free, pressure))
                self.assertTrue(result['derived'])


class ProcessTests(unittest.TestCase):
    def test_tree_wrappers_mlx_independent_runner_and_cycles(self):
        rows = parse_processes(PROCESS_TABLE)
        self.assertEqual({r['pid'] for r in rows}, {10, 11, 12, 13, 14})
        self.assertEqual({r['port'] for r in runner_processes(rows)}, {40001, 40002})
        result = get_ollama_memory_metrics(rows + rows, 10000 * 1024)
        self.assertEqual(result['used_bytes'], 2000 * 1024)
        self.assertEqual(result['process_count'], 5)
        self.assertEqual(result['used_percent_of_system_ram'], 20)

    def test_failed_ps_is_not_zero(self):
        for output in [None, '', 'garbage']:
            self.assertIsNone(get_ollama_memory_metrics(parse_processes(output), 100)['used_bytes'])
        self.assertEqual(get_ollama_memory_metrics(parse_processes(PROCESS_TABLE.splitlines()[0]), 100)['used_bytes'], 0)

    def test_runner_port_validation(self):
        self.assertEqual(runner_processes([{**RUNNER, 'command':'ollama runner --port 70000'}]), [])


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'server.log'
        self.path.write_text('n_past=3999\n')
        self.reader = LogReader(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, slots, models=None, rows=None):
        with patch('ollama_monitor.context.http_json', return_value=slots):
            return get_context_metrics(MODELS if models is None else models,
                                       [RUNNER] if rows is None else rows, self.reader)

    def test_allocated_context_is_not_used_and_old_logs_ignored(self):
        result = self.collect(None)
        self.assertIsNone(result['used_tokens'])
        self.assertIsNone(result['used_percent'])
        self.assertEqual(result['max_tokens'], 4096)

    def test_nested_speculative_state_uses_max_not_sum(self):
        self.assertEqual(_extract_n_decoded([{'n_decoded':4},{'target':{'n_decoded':9}}]), 9)
        self.assertIsNone(numeric(True))
        self.assertIsNone(numeric(float('inf')))
        self.assertIsNone(numeric(float('nan')))

    def test_active_slot_and_its_own_capacity(self):
        result = self.collect([{'id':0,'n_ctx':2048,'n_past':500},
                               {'id':1,'n_ctx':1024,'n_past':700,'is_processing':True}])
        self.assertEqual((result['used_tokens'], result['max_tokens'], result['slot']), (700,1024,1))
        self.assertEqual(result['used_percent'], 68.36)

    def test_zero_is_valid_but_missing_is_null(self):
        self.assertEqual(self.collect([{'n_ctx':100, 'n_past':0}])['used_percent'], 0)
        self.assertIsNone(self.collect([{'n_ctx':100}])['used_percent'])

    def test_multiple_models_dont_borrow_capacity(self):
        result = self.collect([{'n_past':100}], models=MODELS + [{**MODELS[0], 'name':'model-b'}])
        self.assertIsNone(result['max_tokens'])
        self.assertIsNone(result['model_name'])
        self.assertIsNone(result['used_percent'])

    def test_recent_log_fallback_expires_and_resets_on_model_switch(self):
        self.collect(None)
        with self.path.open('a') as f:
            f.write('n_past=50\n')
        result = self.collect(None)
        self.assertEqual(result['used_tokens'], 50)
        self.assertTrue(result['estimated'])
        self.reader.observed_at = time.time() - 31
        self.assertIsNone(self.collect(None)['used_tokens'])
        self.assertIsNone(self.collect(None, models=[{**MODELS[0], 'name':'new'}])['used_tokens'])
        self.assertIsNone(self.collect(None, models=[])['used_tokens'])

    def test_log_rotation_truncation_and_partial_lines(self):
        self.reader.read('same')
        with self.path.open('a') as f: f.write('n_past=')
        self.assertEqual(self.reader.read('same')['last_n_past'], -1)
        with self.path.open('a') as f: f.write('123\n')
        self.assertEqual(self.reader.read('same')['last_n_past'], 123)
        self.path.write_text('')
        self.assertEqual(self.reader.read('same')['last_n_past'], -1)
        self.path.unlink(); self.path.write_text('n_past=999\n')
        self.assertEqual(self.reader.read('same')['last_n_past'], -1)

    def test_no_log_fallback_for_parallel_slots(self):
        self.collect(None)
        with self.path.open('a') as f: f.write('n_past=123\n')
        self.assertIsNone(self.collect([{'id':0},{'id':1}])['used_tokens'])

    def test_new_request_does_not_reuse_previous_occupancy(self):
        self.collect(None)
        with self.path.open('a') as f: f.write('n_past=123\nslot released n_cache_tokens=140\n')
        self.assertEqual(self.collect(None)['used_tokens'], 140)
        with self.path.open('a') as f: f.write('task.n_tokens=20\n')
        self.assertIsNone(self.collect(None)['used_tokens'])


class HistoryTests(unittest.TestCase):
    def test_hour_ranges_and_raw_peaks_and_nulls(self):
        history = History()
        for i in range(2200):
            s = snapshot(i*2)
            s['system_memory']['used_percent'] = 99 if i == 2150 else 50
            history.append(s)
        self.assertEqual(len(history.samples), 2000)
        self.assertEqual(len(history.query('1h',4398)['samples']), 1801)
        self.assertEqual(len(history.query('15m',4398)['samples']), 451)
        self.assertEqual(len(history.query('1m',4398)['samples']), 31)
        points = history.query('1h',4398)['samples']
        self.assertIn(99, [p['system_ram_percent'] for p in points])
        self.assertTrue(all(p['context_percent'] is None for p in points))

    def test_short_interval_retains_hour_and_returned_data_is_detached(self):
        history = History(.5)
        self.assertGreaterEqual(history.samples.maxlen, 7201)
        s = snapshot();history.append(s);s['context']['max_tokens'] = 1
        current = history.status();current['context']['max_tokens'] = 2
        self.assertEqual(history.status()['context']['max_tokens'], 4096)

    def test_collecting_does_not_need_clients(self):
        monitor = Monitor(interval=.02)
        with patch.object(monitor, 'build_snapshot', side_effect=snapshot):
            monitor.start();time.sleep(.09);monitor.stop()
        self.assertGreaterEqual(len(monitor.history.samples), 3)


class OllamaTests(unittest.TestCase):
    def test_offline_restart_version_refresh_and_no_models(self):
        with patch('ollama_monitor.ollama.http_json', side_effect=[None, {'version':'a'}, {'version':'b'}]):
            self.assertFalse(get_ollama_status('http://local', None)['online'])
            self.assertEqual(get_ollama_status('http://local', [])['version'], 'a')
            self.assertEqual(get_ollama_status('http://local', [])['version'], 'b')
        with patch('ollama_monitor.ollama.http_json', return_value={'models':[]}):
            self.assertEqual(get_loaded_models('http://local'), [])
        with patch('ollama_monitor.ollama.http_json', return_value={'error':'not ready'}):
            self.assertIsNone(get_loaded_models('http://local'))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = History()
        cls.server = make_server(cls.history, 0)
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def test_read_only_loopback_cors_and_host(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        self.history.append(snapshot())
        with urlopen(Request(self.url+'/api/status', headers={'Origin':'null'})) as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'null')
            self.assertIsNone(json.load(response)['context']['used_percent'])
        for path, method, headers, code in [('/api/history?range=bad','GET',{},400),
                                            ('/shell','GET',{},404),
                                            ('/api/status','POST',{},501),
                                            ('/api/status','GET',{'Origin':'https://evil.test'},403),
                                            ('/api/status','GET',{'Host':'evil.test'},403)]:
            with self.subTest(path=path, headers=headers), self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url+path, method=method, headers=headers))
            self.assertEqual(error.exception.code, code)
            error.exception.close()
        for range_name in ('1m','15m','1h'):
            with urlopen(self.url+'/api/history?range='+range_name) as response:
                self.assertEqual(json.load(response)['range'], range_name)

    def test_app_served_with_actual_port(self):
        with urlopen(self.url) as response:
            html = response.read().decode()
            self.assertIn("const API_BASE = '"+self.url+"'", html)
            self.assertIn('application/ld+json', html)

    def test_mcp_discovery_window_csp_and_read_only_tools(self):
        result = dispatch('resources/list', {}, self.server.server_port)['resources'][0]
        self.assertTrue(result['_meta']['window']['resizable'])
        self.assertEqual(result['_meta']['ui']['csp']['connectDomains'], [self.url])
        self.assertIn('Ollama Monitor', dispatch('resources/read', {'uri':URI}, 12345)['contents'][0]['text'])
        self.assertTrue(dispatch('tools/list', {}, 12345)['tools'][0]['annotations']['readOnlyHint'])
        with self.assertRaises(ValueError):
            dispatch('tools/call', {'name':'shell'}, 12345)
        self.history.append(snapshot())
        result = dispatch('tools/call', {'name':'monitor_read','arguments':{'range':'1m'}}, self.server.server_port)
        self.assertEqual(result['structuredContent']['history']['range'], '1m')


if __name__ == '__main__':
    unittest.main()
