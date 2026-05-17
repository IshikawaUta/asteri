import os
import socket
import unittest
from unittest.mock import patch, MagicMock
from asteri.arbiter import Arbiter

class TestSystemdSocketActivation(unittest.TestCase):
    @patch("asteri.arbiter.socket.fromfd")
    @patch("asteri.arbiter.Arbiter.setup_signals")
    @patch("asteri.arbiter.Arbiter.manage_workers")
    def test_systemd_socket_activation(self, mock_manage_workers, mock_setup_signals, mock_fromfd):
        # Mock fromfd to return standard mock sockets
        mock_sock3 = MagicMock()
        mock_sock4 = MagicMock()
        mock_fromfd.side_effect = [mock_sock3, mock_sock4]
        
        env = {
            "LISTEN_FDS": "2",
            "LISTEN_PID": str(os.getpid())
        }
        
        with patch.dict(os.environ, env):
            arbiter = Arbiter(
                app_path="example_wsgi:app",
                worker_class="sync",
                num_workers=2,
                binds=["127.0.0.1:8000"] # Should be skipped
            )
            
            # Start arbiter
            arbiter.start()
            
            # Verify socket.fromfd was called for fd 3 and 4
            self.assertEqual(mock_fromfd.call_count, 2)
            mock_fromfd.assert_any_call(3, socket.AF_INET, socket.SOCK_STREAM)
            mock_fromfd.assert_any_call(4, socket.AF_INET, socket.SOCK_STREAM)
            
            # Verify the inherited sockets were appended
            self.assertIn(mock_sock3, arbiter.socks)
            self.assertIn(mock_sock4, arbiter.socks)
            
            # Verify mock_sock3 setblocking was called
            mock_sock3.setblocking.assert_called_with(False)
            mock_sock4.setblocking.assert_called_with(False)

if __name__ == "__main__":
    unittest.main()
