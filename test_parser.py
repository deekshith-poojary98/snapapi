import json

class TestParser:
    def parse(self, test_file):
        with open(test_file, 'r') as file:
            lines = file.readlines()

        suite = {}
        tests = []
        test_map = {}
        current_test = None
        base_url = None

        for line in lines:
            line = line.strip()
            if line.startswith('/') or line == '':
                continue
            elif line.startswith('SUITE:'):
                suite['name'] = line.replace('SUITE: ', '')
            elif line.startswith('DESC:'):
                if 'name' in suite and 'description' not in suite:
                    suite['description'] = line.replace('DESC: ', '')
                elif current_test:
                    current_test['description'] = line.replace('DESC: ', '')
            elif line.startswith('URL:'):
                if current_test:
                    current_test['base_url'] = line.replace('URL: ', '')
                else:
                    base_url = line.replace('URL: ', '')
            elif line.startswith('OPTIONS:'):
                options = json.loads(line.replace('OPTIONS: ', ''))
                suite['options'] = options
            elif line.startswith('TEST:'):
                if current_test:
                    tests.append(current_test)
                current_test = {
                    'name': line.replace('TEST: ', ''),
                    'steps': [],
                    'base_url': base_url
                }
                test_map[current_test['name']] = current_test
            elif line.startswith('TAG:'):
                current_test['tag'] = line.replace('TAG: ', '')
            elif line.startswith('SETUP:'):
                current_test['setup'] = line.replace('SETUP: ', '')
            elif line.startswith('TEARDOWN:'):
                current_test['teardown'] = line.replace('TEARDOWN: ', '')
            elif line.startswith('REQUEST:'):
                action, endpoint = line.replace('REQUEST: ', '').split(' ', 1)
                current_test['steps'].append({'action': action, 'endpoint': endpoint})
            elif line.startswith('DATA:'):
                data = json.loads(line.replace('DATA: ', ''))
                current_test['steps'].append({'data': data})
            elif line.startswith('EXPECT:'):
                check_type, check_value = line.replace('EXPECT: ', '').split(' ', 1)
                current_test['steps'][-1].setdefault('checks', []).append({'type': check_type, 'value': check_value})

        if current_test:
            tests.append(current_test)

        suite['tests'] = tests
        suite['test_map'] = test_map
        suite.setdefault('options', {'STOP-ON-FAILURE': True})  # Set default options if not provided
        return suite
