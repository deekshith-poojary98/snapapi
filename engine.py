from test_parser import TestParser
from api_client import APIClient


class Engine:
    def __init__(self, test_file):
        self.parser = TestParser()
        self.suite = self.parser.parse(test_file)

    def run(self):
        print(f"Running test suite: {self.suite['name']}")
        if 'description' in self.suite:
            print(f"Description: {self.suite['description']}")
        for test in self.suite['tests']:
            self.run_test(test['name'])

    def run_test(self, test_name):
        test = self.suite['test_map'][test_name]
        print(f"\nRunning test: {test['name']}")
        if 'description' in test:
            print(f"Description: {test['description']}")
        if 'tag' in test:
            print(f"Tag: {test['tag']}")

        if 'setup' in test:
            self.run_test(test['setup'])

        client = APIClient(test.get('base_url', self.suite.get('base_url')))
        for step in test['steps']:
            self.execute_step(client, step)

        if 'teardown' in test:
            self.run_test(test['teardown'])

    def execute_step(self, client, step):
        action = step['action']
        endpoint = step['endpoint']
        data = step.get('data', {})
        if action == 'GET':
            self.response = client.get(endpoint)
            print(self.response.json())
        elif action == 'POST':
            self.response = client.post(endpoint, json=data)
        elif action == 'DELETE':
            self.response = client.delete(endpoint)
        elif action == 'PUT':
            self.response = client.put(endpoint, json=data)
        elif action == 'PATCH':
            self.response = client.patch(endpoint, json=data)

        checks = step.get('checks', [])
        for check in checks:
            self.execute_check(check)

    def execute_check(self, check):
        check_type = check['type']
        value = check['value']
        if check_type == 'STATUS':
            assert self.response.status_code == int(value), f"Expected {value}, got {self.response.status_code}"
        elif check_type == 'CONTAINS':
            assert value in self.response.text, f"Response does not contain {value}"
