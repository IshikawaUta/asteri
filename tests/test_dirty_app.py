import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from asteri.dirty import DirtyAppLoader


class TestDirtyApp(unittest.TestCase):
    @patch("asteri.dirty.import_app")
    def test_wsgi_routing_by_host_and_path(self, mock_import_app):
        # Setup mock applications
        app_host = MagicMock()
        app_path = MagicMock()
        app_default = MagicMock()

        # Mapping mock imports
        mock_import_app.side_effect = lambda s: {
            "host_app:app": app_host,
            "path_app:app": app_path,
            "default_app:app": app_default,
        }.get(s)

        loader = DirtyAppLoader(
            "example.com=host_app:app,/api=path_app:app,default=default_app:app"
        )

        # 1. Match Host
        environ_host = {"HTTP_HOST": "example.com", "PATH_INFO": "/hello"}
        loader(environ_host, MagicMock())
        app_host.assert_called_once()

        # 2. Match Path prefix
        environ_path = {"HTTP_HOST": "other.com", "PATH_INFO": "/api/v1/users"}
        loader(environ_path, MagicMock())
        app_path.assert_called_once()

        # 3. Match Default fallback
        environ_default = {"HTTP_HOST": "unknown.com", "PATH_INFO": "/hello"}
        loader(environ_default, MagicMock())
        app_default.assert_called_once()

    @patch("asteri.dirty.import_app")
    def test_asgi_routing_by_host_and_path(self, mock_import_app):
        app_host = AsyncMock()
        app_path = AsyncMock()

        mock_import_app.side_effect = lambda s: {
            "host_app:app": app_host,
            "path_app:app": app_path,
        }.get(s)

        loader = DirtyAppLoader("example.com=host_app:app,/api=path_app:app")

        # Mock receive and send async functions
        async_recv = AsyncMock()
        async_send = AsyncMock()

        # 1. ASGI Host match
        async def run_host_test():
            scope = {
                "type": "http",
                "headers": [(b"host", b"example.com")],
                "path": "/hello",
            }
            await loader(scope, async_recv, async_send)
            app_host.assert_called_once_with(scope, async_recv, async_send)

        import asyncio

        asyncio.run(run_host_test())


if __name__ == "__main__":
    unittest.main()
