#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>
#include <string.h>

/* Scan raw_data and return a tuple: (method, path, version, headers_dict, body_bytes) or None if malformed. */
static PyObject* fastparser_parse_http(PyObject* self, PyObject* args) {
    Py_buffer view;
    if (!PyArg_ParseTuple(args, "y*", &view)) {
        return NULL;
    }
    
    const char* raw = (const char*)view.buf;
    Py_ssize_t len = view.len;
    
    // Find double CRLF division (\r\n\r\n) separating headers and body
    Py_ssize_t double_crlf_pos = -1;
    for (Py_ssize_t i = 0; i < len - 3; i++) {
        if (raw[i] == '\r' && raw[i+1] == '\n' && raw[i+2] == '\r' && raw[i+3] == '\n') {
            double_crlf_pos = i;
            break;
        }
    }
    
    if (double_crlf_pos == -1) {
        PyBuffer_Release(&view);
        Py_RETURN_NONE;
    }
    
    // Construct body bytes starting after \r\n\r\n
    PyObject* body_bytes = PyBytes_FromStringAndSize(raw + double_crlf_pos + 4, len - (double_crlf_pos + 4));
    
    // Construct headers dictionary
    PyObject* headers_dict = PyDict_New();
    
    Py_ssize_t line_start = 0;
    int is_first_line = 1;
    PyObject* method_obj = NULL;
    PyObject* path_obj = NULL;
    PyObject* version_obj = NULL;
    
    while (line_start < double_crlf_pos) {
        Py_ssize_t line_end = line_start;
        while (line_end < double_crlf_pos && !(raw[line_end] == '\r' && raw[line_end+1] == '\n')) {
            line_end++;
        }
        
        Py_ssize_t line_len = line_end - line_start;
        if (line_len > 0) {
            if (is_first_line) {
                is_first_line = 0;
                // Parse request line (e.g. GET /path HTTP/1.1)
                Py_ssize_t first_space = -1;
                Py_ssize_t second_space = -1;
                for (Py_ssize_t j = 0; j < line_len; j++) {
                    if (raw[line_start + j] == ' ') {
                        if (first_space == -1) {
                            first_space = j;
                        } else if (second_space == -1) {
                            second_space = j;
                            break;
                        }
                    }
                }
                
                if (first_space != -1 && second_space != -1) {
                    method_obj = PyUnicode_DecodeLatin1(raw + line_start, first_space, NULL);
                    path_obj = PyUnicode_DecodeLatin1(raw + line_start + first_space + 1, second_space - first_space - 1, NULL);
                    version_obj = PyUnicode_DecodeLatin1(raw + line_start + second_space + 1, line_len - second_space - 1, NULL);
                } else {
                    // Invalid request line format
                    Py_XDECREF(headers_dict);
                    Py_XDECREF(body_bytes);
                    PyBuffer_Release(&view);
                    Py_RETURN_NONE;
                }
            } else {
                // Parse header line: "Key: Value"
                Py_ssize_t colon_pos = -1;
                for (Py_ssize_t j = 0; j < line_len; j++) {
                    if (raw[line_start + j] == ':') {
                        colon_pos = j;
                        break;
                    }
                }
                
                if (colon_pos != -1) {
                    // Strip and lowercase key
                    Py_ssize_t k_start = 0;
                    while (k_start < colon_pos && (raw[line_start + k_start] == ' ' || raw[line_start + k_start] == '\t')) {
                        k_start++;
                    }
                    Py_ssize_t k_end = colon_pos;
                    while (k_end > k_start && (raw[line_start + k_end - 1] == ' ' || raw[line_start + k_end - 1] == '\t')) {
                        k_end--;
                    }
                    
                    // Strip value
                    Py_ssize_t v_start = colon_pos + 1;
                    while (v_start < line_len && (raw[line_start + v_start] == ' ' || raw[line_start + v_start] == '\t')) {
                        v_start++;
                    }
                    Py_ssize_t v_end = line_len;
                    while (v_end > v_start && (raw[line_start + v_end - 1] == ' ' || raw[line_start + v_end - 1] == '\t')) {
                        v_end--;
                    }
                    
                    // Create lowercased key dynamically
                    char* key_buf = malloc(k_end - k_start + 1);
                    if (key_buf) {
                        for (Py_ssize_t j = 0; j < k_end - k_start; j++) {
                            char c = raw[line_start + k_start + j];
                            if (c >= 'A' && c <= 'Z') c = c + 32; // Direct fast lowercasing
                            key_buf[j] = c;
                        }
                        key_buf[k_end - k_start] = '\0';
                        
                        PyObject* key_obj = PyUnicode_FromStringAndSize(key_buf, k_end - k_start);
                        free(key_buf);
                        
                        PyObject* val_obj = PyUnicode_DecodeLatin1(raw + line_start + v_start, v_end - v_start, NULL);
                        
                        if (key_obj && val_obj) {
                            PyDict_SetItem(headers_dict, key_obj, val_obj);
                        }
                        Py_XDECREF(key_obj);
                        Py_XDECREF(val_obj);
                    }
                }
            }
        }
        line_start = line_end + 2;
    }
    
    PyBuffer_Release(&view);
    
    if (!method_obj || !path_obj || !version_obj) {
        Py_XDECREF(headers_dict);
        Py_XDECREF(body_bytes);
        Py_XDECREF(method_obj);
        Py_XDECREF(path_obj);
        Py_XDECREF(version_obj);
        Py_RETURN_NONE;
    }
    
    PyObject* res_tuple = PyTuple_Pack(5, method_obj, path_obj, version_obj, headers_dict, body_bytes);
    Py_DECREF(method_obj);
    Py_DECREF(path_obj);
    Py_DECREF(version_obj);
    Py_DECREF(headers_dict);
    Py_DECREF(body_bytes);
    
    return res_tuple;
}

/* Scan raw uWSGI data and return tuple: (vars_dict, modifier1) or None if malformed. */
static PyObject* fastparser_parse_uwsgi(PyObject* self, PyObject* args) {
    Py_buffer view;
    if (!PyArg_ParseTuple(args, "y*", &view)) {
        return NULL;
    }
    
    const unsigned char* raw = (const unsigned char*)view.buf;
    Py_ssize_t len = view.len;
    
    if (len < 4) {
        PyBuffer_Release(&view);
        Py_RETURN_NONE;
    }
    
    unsigned char modifier1 = raw[0];
    unsigned short size = raw[1] | (raw[2] << 8); // uWSGI little-endian size
    
    if (len < 4 + size) {
        PyBuffer_Release(&view);
        Py_RETURN_NONE;
    }
    
    PyObject* vars_dict = PyDict_New();
    Py_ssize_t pos = 4;
    Py_ssize_t end = 4 + size;
    
    while (pos < end) {
        if (pos + 2 > end) break;
        unsigned short key_len = raw[pos] | (raw[pos+1] << 8);
        pos += 2;
        if (pos + key_len > end) break;
        PyObject* key_obj = PyUnicode_DecodeLatin1((const char*)raw + pos, key_len, NULL);
        pos += key_len;
        
        if (pos + 2 > end) break;
        unsigned short val_len = raw[pos] | (raw[pos+1] << 8);
        pos += 2;
        if (pos + val_len > end) break;
        PyObject* val_obj = PyUnicode_DecodeLatin1((const char*)raw + pos, val_len, NULL);
        pos += val_len;
        
        if (key_obj && val_obj) {
            PyDict_SetItem(vars_dict, key_obj, val_obj);
        }
        Py_XDECREF(key_obj);
        Py_XDECREF(val_obj);
    }
    
    PyBuffer_Release(&view);
    
    PyObject* mod_obj = PyLong_FromLong(modifier1);
    PyObject* res_tuple = PyTuple_Pack(2, vars_dict, mod_obj);
    Py_DECREF(vars_dict);
    Py_DECREF(mod_obj);
    
    return res_tuple;
}

static PyMethodDef FastParserMethods[] = {
    {"parse_http", fastparser_parse_http, METH_VARARGS, "Parse raw HTTP request at C-speed"},
    {"parse_uwsgi", fastparser_parse_uwsgi, METH_VARARGS, "Parse raw uWSGI request at C-speed"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef fastparsermodule = {
    PyModuleDef_HEAD_INIT,
    "fastparser",
    "High-performance C extension for parsing HTTP and uWSGI protocols",
    -1,
    FastParserMethods
};

PyMODINIT_FUNC PyInit_fastparser(void) {
    return PyModule_Create(&fastparsermodule);
}
