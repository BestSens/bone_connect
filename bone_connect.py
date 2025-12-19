import socket
import struct
import json
from typing import Optional
import numpy as np
import hashlib
from threading import Lock

def bytes_to_amplitude(byte_data) -> float:
    uint32 = np.frombuffer(
        byte_data,
        dtype=np.dtype(np.uint32).newbyteorder("B"),
        count=len(byte_data) // 4,
    )[0]
    return ((uint32 & 0xfff) * (5.0 / 4096.0) - 2.5) * 2

def bytes_to_runtime(byte_data) -> float:
    data = np.frombuffer(
        byte_data,
        dtype=np.dtype(np.uint32).newbyteorder("B"),
        count=len(byte_data) // 4,
    )[0]
    data = (data // 4096) * 100
    if data & 0x1 == 0:
        data = data / 512.0
    else:
        data = (data // 16) / 128.0

    return data

def bytes_to_float(byte_data) -> list[float]:
    return np.frombuffer(
        byte_data,
        dtype=np.dtype(np.float32).newbyteorder("B"),
        count=len(byte_data) // 4,
    ).tolist()

class bone_connect():
    __socket = socket.socket()
    __host = ""
    __port = 6450
    __api = 2
    __lock = Lock()

    def __init__(
        self,
        host: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        port: int = 6450,
        api: int = 2,
    ):
        self.__host = host
        self.__port = port
        self.__addrinfo = socket.getaddrinfo(self.__host, self.__port, socket.AF_UNSPEC, socket.IPPROTO_IP)[0]
        self.__socket = socket.socket(self.__addrinfo[0], socket.SOCK_STREAM)
        self.__socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        self.__api = api
        self.connect()
        if username and password:
            self.login(username, password)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__socket.close()

    def __del__(self):
        self.__socket.close()

    def connect(self):
        self.__socket.connect(self.__addrinfo[4])

    @staticmethod
    def get_ipv6_link_local_address_from_serial(serial_number):
        if serial_number.startswith('SN208'):
            serial_number = serial_number[5:]

        hex_val = "{:04x}".format(int(serial_number))
        return f"fe80::b5:b1ff:fe{hex_val[:2]}:{hex_val[2:]}"

    def send_message(self, message: dict):
        with self.__lock:
            if 'api' not in message:
                message['api'] = self.__api

            message_str = json.dumps(message) + "\n"
            self.__socket.sendall(message_str.encode())

            amount_received = 0
            amount_expected = int(self.__socket.recv(8, socket.MSG_WAITALL), 16)

            data = bytes()

            while amount_received < amount_expected:
                data_temp = self.__socket.recv(2048)
                amount_received += len(data_temp)

                data = data + data_temp

            return json.loads(data.decode())

    def login(self, username: str, password: str):
        token = self.send_message({'command': 'request_token'})['payload']['token']

        signed_token = hashlib.sha512(password.encode()).hexdigest() + token
        signed_token = hashlib.sha512(signed_token.encode()).hexdigest()

        auth = self.send_message({'command': 'auth', 'payload': {'username': username, 'signed_token': signed_token}})

        if 'payload' in auth and 'error' in auth['payload']:
            raise Exception("error on login: " + auth['payload']['error'])

    def dv_data(self):
        with self.__lock:
            message = '{"command":"dv_data"}\n'
            self.__socket.sendall(message.encode())

            amount_received = 0
            amount_expected = int(self.__socket.recv(8, socket.MSG_WAITALL), 16)

            data = bytes()

            while amount_received < amount_expected:
                data_temp = self.__socket.recv(2048)
                amount_received += len(data_temp)
                data = data + data_temp

        expected_list_len = len(data) // 3
        retval = [0.] * expected_list_len

        for n in range(expected_list_len):
            x = int(data[n * 3:n * 3 + 3], base=16)
            retval[n] = (x - 2048.) / 4096. * 5.

        return retval

    def ks(self, channel: int, amount: int, last_position: int = 0, unit = 'G'):
        with self.__lock:
            message = json.dumps({'command': 'ks', 'payload': {'channel': channel, 'amount': amount, 'start': last_position, 'float': True}}) + "\n"
            self.__socket.sendall(message.encode())

            amount_received = 0
            amount_expected = int(self.__socket.recv(8, socket.MSG_WAITALL), 16)

            data = bytes()

            while amount_received < amount_expected:
                data_temp = self.__socket.recv(2048)
                amount_received += len(data_temp)

                data = data + data_temp

        last_position = int.from_bytes(data[:4], "big")
        data = data[4:]

        data_out = [0.] * ((amount_received - 4) // 4)
        i = 0
        n = 0
        while i < len(data):
            data_out[n], = struct.unpack('>f', data[i:i+4])
            i += 4
            n += 1

        return last_position, data_out

    def ks_sync(self, amount: int, last_position: int = 0, filter = [0, 1, 2], unit = 'G'):
        with self.__lock:
            message = json.dumps({'command': 'ks_sync', 'payload': {'amount': amount, 'start': last_position, 'filter': filter, 'unit': unit}}) + "\n"
            self.__socket.sendall(message.encode())

            amount_received = 0
            amount_expected = int(self.__socket.recv(8, socket.MSG_WAITALL), 16)

            input_data = bytes()

            while amount_received < amount_expected:
                data_temp = self.__socket.recv(2048)
                amount_received += len(data_temp)

                data = input_data + data_temp

        last_position = int.from_bytes(input_data[:4], "big")
        input_data = input_data[4:]

        data = np.zeros((len(filter), int((amount_received - 4) / (5 * len(filter)))))
        i = 0
        n = 0
        while i < len(data):
            for j in range(len(filter)):
                data[j][n], = struct.unpack('>f', data[i+1:i+5])
                i = i + 5

            n = n + 1

        return last_position, data

    def sync(self, amount: int, last_position: int = 0, filter = ["saw", "int", "coe", "int2"]):
        with self.__lock:
            message = json.dumps({'command': 'sync', 'payload': {'amount': amount, 'start': last_position, 'filter': filter}}) + "\n"
            self.__socket.sendall(message.encode())

            amount_received = 0
            amount_expected = int(self.__socket.recv(8, socket.MSG_WAITALL), 16)

            input_data = bytes()

            while amount_received < amount_expected:
                data_temp = self.__socket.recv(2048)
                amount_received += len(data_temp)

                input_data = input_data + data_temp

        last_position = int.from_bytes(input_data[:4], "big")
        input_data = input_data[4:]

        data = dict()

        def calc_saw(buffer):
            out_rt = []
            out_amp = []

            for i in range (0, len(buffer), 4):
                subrange = input_data[i:i+4]
                out_rt.append(bytes_to_runtime(subrange))
                out_amp.append(bytes_to_amplitude(subrange))

            return out_rt, out_amp

        split_val = len(input_data) // len(filter)
        pos = 0

        for f in filter:
            subspan = input_data[pos:pos+split_val]

            if f == 'saw':
                rt, amp = calc_saw(subspan)
                data['rt'] = rt
                data['amp'] = amp
            else:
                data[f] = bytes_to_float(subspan)

            pos += split_val

        return last_position, data
