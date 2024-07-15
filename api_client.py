import requests

class APIClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def get(self, endpoint, **kwargs):
        return requests.get(f"{self.base_url}{endpoint}", **kwargs)

    def post(self, endpoint, data=None, **kwargs):
        return requests.post(f"{self.base_url}{endpoint}", json=data, **kwargs)

    def put(self, endpoint, data=None, **kwargs):
        return requests.put(f"{self.base_url}{endpoint}", json=data, **kwargs)

    def delete(self, endpoint, **kwargs):
        return requests.delete(f"{self.base_url}{endpoint}", **kwargs)

    def patch(self, endpoint, data=None, **kwargs):
        return requests.patch(f"{self.base_url}{endpoint}", json=data, **kwargs)



if __name__ == "__main":
    client = APIClient("https://api.example.com")

    # GET request
    response = client.get("/users")
    print(response.json())

    # POST request
    new_user_data = {"name": "John", "email": "john@example.com"}
    response = client.post("/users", data=new_user_data)
    print(response.json())

    # PUT request
    update_user_data = {"name": "John Doe"}
    response = client.put("/users/1", data=update_user_data)
    print(response.json())

    # DELETE request
    response = client.delete("/users/1")
    print(response.status_code)

    # PATCH request
    update_partial_data = {"email": "john.doe@example.com"}
    response = client.patch("/users/1", data=update_partial_data)
    print(response.json())
