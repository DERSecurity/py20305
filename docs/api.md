# Management API

An optional HTTP API for observing a running client and nudging it — refresh a
measurement, reconnect, swap a certificate. It is read-mostly; the client does
its job without it.

Install the `api` extra.

## Standalone

```python
import uvicorn
from py20305.api import create_app, ClientAPIService

service = ClientAPIService(client=client, telemetry=telemetry, der_resources=resources)
app = create_app(service)
uvicorn.run(app, host="127.0.0.1", port=8080)
```

Interactive docs are at `/docs`, the OpenAPI schema at `/openapi.json`, and the
endpoints under `/api/v1`.

## Before the client has connected

The API is most useful when the client *cannot* reach the server, so the app
does not require a service up front. Pass a callable and it is consulted per
request:

```python
app = create_app(lambda: my_service_or_none)
```

Until it returns something, the routes report `not_connected` rather than
failing. That is what lets the API stay up across a connection failure and tell
you why.

## Inside an app you already have

If you are embedding this client in your own service, mount the router rather
than taking our application:

```python
from fastapi import FastAPI
from py20305.api import create_client_router

app = FastAPI()
app.include_router(create_client_router(lambda: service), prefix="/csip")
```

## Adding endpoints of your own

`ClientAPIService` is designed to be subclassed. An application managing many
devices extends it rather than reimplementing it:

```python
from py20305.api import ClientAPIService

class MyService(ClientAPIService):
    def get_fleet_summary(self) -> dict:
        ...
```

Build your own router for the additions and include both.
