# SnapAPI

SnapAPI is a lightweight and efficient API testing framework designed for simplicity and speed. Write your tests in easy-to-understand syntax and let SnapAPI handle the rest.

## Key Features

- **Simple Syntax**: Write tests in a straightforward, human-readable format.
- **Flexible Test Cases**: Supports GET, POST, and DELETE requests with ease.
- **Setup and Teardown**: Define setup and teardown actions within your test suite.
- **High Performance**: Designed for quick execution and efficient performance.
- **Extensible**: Easily add new features and checks as needed.

## Getting Started

### Prerequisites

- Python 3.6 or higher
- Requests library: `pip install requests`

### Installation

Clone the repository:

```
git clone https://github.com/yourusername/snapapi.git
cd snapapi
```

## Usage

Define your test suite: Create a file with .test extension, for example, test_suite.test.
```
SUITE: Book Store Application 
DESC: This test suite validates the API calls

URL: https://api.example.com

TEST: Setup User
DESC: This test case sets up a user
TAG: setup
  REQUEST: POST /users
  DATA: { "name": "John", "email": "john@example.com" }
  EXPECT: STATUS 201
  EXPECT: CONTAINS id

TEST: Verify User Endpoint
DESC: This test case validates the userId
TAG: user-endpoint
SETUP: Setup User
  REQUEST: GET /users
  EXPECT: STATUS 200
  EXPECT: CONTAINS userId

TEST: Cleanup User
DESC: This test case cleans up the user
TAG: cleanup
  REQUEST: DELETE /users/1
  EXPECT: STATUS 200

TEST: Create New User
DESC: This test case creates a new user
TAG: create-user
URL: https://different-api.example.com
SETUP: Setup User
TEARDOWN: Cleanup User
  REQUEST: POST /users
  DATA: { "name": "Jane", "email": "jane@example.com" }
  EXPECT: STATUS 201
  EXPECT: CONTAINS id
```
Run your test suite: Create a Python script to execute the tests, for example, run_tests.py.
```
from engine import Engine

engine = Engine('test_suite.test')
engine.run()
```
Run your script:
```
python run_tests.py
```

## Project Structure
```
snapapi/
├── parser.py         # Parses the test files
├── engine.py         # Executes the tests based on the parsed data
├── api_client.py     # Handles the API requests
├── README.md         # This file
└── tests/            # Directory for your test files
```

## Contributing
We welcome contributions! Please read our contributing guidelines for more details.

## License
This project is licensed under the MIT License - see the LICENSE file for details.


