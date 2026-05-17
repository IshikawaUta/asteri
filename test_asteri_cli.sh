#!/bin/bash

# ==============================================================================
# ASTERI FULL REGRESSION TEST SCRIPT (V6 - LOCAL DEVELOPMENT MODE)
# ==============================================================================

# Basic Configuration
APP="test_app:app"
TEST_LOG="test_results.log"
TIMEOUT=5
USER_NAME=$(whoami)
ASTERI="python3 -m asteri"

# Output Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "-------------------------------------------" > $TEST_LOG
echo "ASTERI CLI FULL TEST LOG - $(date)" >> $TEST_LOG
echo "-------------------------------------------" >> $TEST_LOG

setup_dummy_files() {
    echo -e "${BLUE}Setting up dummy files...${NC}"
    rm -f cli_test.pid
    echo "workers = 1" > test_asteri_conf.py
    echo "def app(environ, start_response): start_response('200 OK', [('Content-Type', 'text/plain')]); return [b'OK']" > test_app.py
    if command -v openssl > /dev/null; then
        openssl req -x509 -newkey rsa:2048 -keyout test_key.pem -out test_cert.pem -days 1 -nodes -subj "/CN=localhost" > /dev/null 2>&1
    else
        touch test_key.pem test_cert.pem
    fi
}

cleanup_dummy_files() {
    echo -e "${BLUE}Cleaning up test files...${NC}"
    rm -f test_asteri_conf.py test_key.pem test_cert.pem cli_test.pid cli_access.log cli_error.log cli_capture.log last_error.log test_app.py cli_test_ctrl.sock
}

run_test() {
    local desc=$1
    local cmd=$2
    local type=$3 

    echo -n "Testing [$desc]... "
    local tmp_err="last_error.log"
    rm -f $tmp_err

    if [ "$type" == "immediate" ]; then
        if eval "$cmd" > $tmp_err 2>&1; then
            echo -e "${GREEN}[SUCCESS]${NC}"
        else
            echo -e "${RED}[FAIL]${NC}"
            echo "FAIL: $desc ($cmd)" >> $TEST_LOG
            cat $tmp_err >> $TEST_LOG
        fi
    elif [ "$type" == "daemon" ]; then
        eval "$cmd" > $tmp_err 2>&1
        sleep 4
        if [ -f "cli_test.pid" ]; then
            local dpid=$(cat cli_test.pid)
            if ps -p $dpid > /dev/null; then
                echo -e "${GREEN}[SUCCESS]${NC}"
                kill $dpid
                wait $dpid 2>/dev/null
            else
                echo -e "${RED}[FAIL]${NC}"
                echo "FAIL: $desc ($cmd) - Daemon exit" >> $TEST_LOG
                cat $tmp_err >> $TEST_LOG
            fi
        else
            echo -e "${RED}[FAIL]${NC}"
            echo "FAIL: $desc ($cmd) - No PID file" >> $TEST_LOG
            cat $tmp_err >> $TEST_LOG
        fi
    else
        eval "exec $cmd" > $tmp_err 2>&1 &
        local pid=$!
        sleep $TIMEOUT
        
        if ps -p $pid > /dev/null; then
            echo -e "${GREEN}[SUCCESS]${NC}"
            kill $pid
            wait $pid 2>/dev/null
        else
            echo -e "${RED}[FAIL]${NC}"
            echo "FAIL: $desc ($cmd) - Exit premature" >> $TEST_LOG
            echo "--- Error Output ---" >> $TEST_LOG
            cat $tmp_err >> $TEST_LOG
            echo "--------------------" >> $TEST_LOG
        fi
    fi
    rm -f $tmp_err
    sleep 1
}

# --- EXECUTION ---
setup_dummy_files

echo -e "\n${YELLOW}[GROUP: CONFIG]${NC}"
run_test "Version" "$ASTERI -v" "immediate"
run_test "Help" "$ASTERI --help" "immediate"
run_test "Check Config" "$ASTERI --check-config -c test_asteri_conf.py $APP" "immediate"
run_test "Print Config" "$ASTERI --print-config -c test_asteri_conf.py $APP" "immediate"
run_test "Custom Config" "$ASTERI -c test_asteri_conf.py -b 127.0.0.1:19281 $APP" "long"

echo -e "\n${YELLOW}[GROUP: NETWORK]${NC}"
run_test "Bind" "$ASTERI -b 127.0.0.1:19282 $APP" "long"
run_test "Backlog" "$ASTERI --backlog 512 -b 127.0.0.1:19283 $APP" "long"
run_test "Reuse Port" "$ASTERI --reuse-port -b 127.0.0.1:19284 $APP" "long"

echo -e "\n${YELLOW}[GROUP: WORKERS]${NC}"
run_test "Sync" "$ASTERI -k sync -w 1 -b 127.0.0.1:19285 $APP" "long"
run_test "GThread" "$ASTERI -k gthread --threads 2 -b 127.0.0.1:19286 $APP" "long"
run_test "ASGI" "$ASTERI -k asgi -b 127.0.0.1:19287 $APP" "long"
run_test "Gevent" "$ASTERI -k gevent -w 1 -b 127.0.0.1:19306 $APP" "long"
run_test "Connections" "$ASTERI --worker-connections 100 -b 127.0.0.1:19288 $APP" "long"
run_test "Max Requests" "$ASTERI --max-requests 50 --max-requests-jitter 5 -b 127.0.0.1:19289 $APP" "long"
run_test "Timeouts" "$ASTERI -t 30 --graceful-timeout 5 --keep-alive 3 -b 127.0.0.1:19290 $APP" "long"
run_test "Preload" "$ASTERI --preload -b 127.0.0.1:19291 $APP" "long"

echo -e "\n${YELLOW}[GROUP: SECURITY]${NC}"
run_test "SSL & Ciphers" "$ASTERI --keyfile test_key.pem --certfile test_cert.pem --ssl-version 2 --ciphers HIGH -b 127.0.0.1:19292 $APP" "long"
run_test "User/Group" "$ASTERI -u $USER_NAME -b 127.0.0.1:19293 $APP" "long"
run_test "Umask" "$ASTERI -m 0022 -b 127.0.0.1:19294 $APP" "long"

echo -e "\n${YELLOW}[GROUP: LOGGING]${NC}"
run_test "Log Files" "$ASTERI --access-logfile cli_access.log --error-logfile cli_error.log -b 127.0.0.1:19295 $APP" "long"
run_test "Log Level" "$ASTERI --log-level debug -b 127.0.0.1:19296 $APP" "long"
run_test "Capture Output" "$ASTERI --capture-output --error-logfile cli_capture.log -b 127.0.0.1:19297 $APP" "long"
run_test "Access Format" "$ASTERI --access-logformat '[%(asctime)s] %(message)s' -b 127.0.0.1:19298 $APP" "long"

echo -e "\n${YELLOW}[GROUP: PROCESS]${NC}"
run_test "Daemon Mode" "$ASTERI -D -p cli_test.pid -b 127.0.0.1:19299 $APP" "daemon"
run_test "PID File" "$ASTERI -p cli_test.pid -b 127.0.0.1:19300 $APP" "long"
run_test "Chdir" "$ASTERI --chdir . -b 127.0.0.1:19301 $APP" "long"
run_test "Env & Name" "$ASTERI -e FOO=BAR -n asteri_test -b 127.0.0.1:19302 $APP" "long"
run_test "Reload" "$ASTERI --reload -b 127.0.0.1:19303 $APP" "long"
run_test "Disable Dashboard" "$ASTERI --disable-dashboard -b 127.0.0.1:19313 $APP" "long"

echo -e "\n${YELLOW}[GROUP: LIMITS & H2]${NC}"
run_test "Limits" "$ASTERI --limit-request-line 1024 --limit-request-fields 20 --limit-request-field_size 4096 -b 127.0.0.1:19304 $APP" "long"
run_test "HTTP/2" "$ASTERI --http-protocols h1,h2 --http2-max-concurrent-streams 50 --keyfile test_key.pem --certfile test_cert.pem -b 127.0.0.1:19305 $APP" "long"

echo -e "\n${YELLOW}[GROUP: ADVANCED FEATURES]${NC}"
run_test "Tornado Worker" "$ASTERI -k tornado -w 1 -b 127.0.0.1:19307 $APP" "long"
run_test "GTornado Worker" "$ASTERI -k gtornado -w 1 -b 127.0.0.1:19308 $APP" "long"
run_test "Control Socket" "$ASTERI --control-socket cli_test_ctrl.sock -b 127.0.0.1:19309 $APP" "long"
run_test "Dirty Apps" "$ASTERI --dirty-apps 'host1:app1' -b 127.0.0.1:19310 $APP" "long"
run_test "Stash Address" "$ASTERI --stash-address '127.0.0.1:9999' -b 127.0.0.1:19311 $APP" "long"
run_test "StatsD Metrics" "$ASTERI --statsd-host 127.0.0.1 --statsd-port 8125 --statsd-prefix my_asteri -b 127.0.0.1:19312 $APP" "long"

echo -e "\n${BLUE}=================================================${NC}"
if [ $(wc -l < $TEST_LOG) -gt 3 ]; then
    echo -e "${RED}RESULT: THERE ARE FAILURES.${NC}"
    echo "Check $TEST_LOG for details."
else
    echo -e "${GREEN}RESULT: ALL COMBINATIONS SUCCESSFUL!${NC}"
    cleanup_dummy_files
    rm $TEST_LOG
fi
