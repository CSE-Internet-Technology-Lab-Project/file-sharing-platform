"""
UDP-based wire protocol for resumable chunk downloads.

Instead of TCP, uses UDP packets with sequence numbers so that downloads
can resume from the exact packet where they stopped, even if network drops.

Packet format:
  - 4 bytes: sequence number (big-endian)
  - 4 bytes: total packets (big-endian)
  - 4 bytes: payload size (big-endian)
  - N bytes: payload data

Client tracks received packets and can resume by requesting the next missing packet.
"""

import socket
import struct
import time
from typing import Optional, Callable


PACKET_HEADER_SIZE = 12  # seq (4) + total (4) + size (4)
MAX_PAYLOAD_PER_PACKET = 65536  # ~64KB per UDP packet (safe for most networks)
UDP_TIMEOUT = 5.0  # seconds
MAX_RETRIES = 3


class UDPPacket:
    """A single UDP packet in the transfer sequence."""
    def __init__(self, seq_num: int, total_packets: int, payload: bytes):
        self.seq_num = seq_num
        self.total_packets = total_packets
        self.payload = payload

    def to_bytes(self) -> bytes:
        """Serialize packet to bytes for transmission."""
        header = struct.pack(
            ">III",
            self.seq_num,
            self.total_packets,
            len(self.payload)
        )
        return header + self.payload

    @staticmethod
    def from_bytes(data: bytes) -> Optional["UDPPacket"]:
        """Deserialize packet from received bytes."""
        if len(data) < PACKET_HEADER_SIZE:
            return None
        try:
            seq_num, total_packets, payload_size = struct.unpack(">III", data[:PACKET_HEADER_SIZE])
            payload = data[PACKET_HEADER_SIZE:PACKET_HEADER_SIZE + payload_size]
            if len(payload) != payload_size:
                return None
            return UDPPacket(seq_num, total_packets, payload)
        except struct.error:
            return None


class UDPSender:
    """Sends data over UDP in sequenced packets."""
    
    def __init__(self, host: str, port: int, timeout: float = UDP_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)

    def send_data(self, data: bytes, progress_callback: Optional[Callable[[int, int], None]] = None) -> bool:
        """
        Send data as a sequence of UDP packets.
        progress_callback(current_packet, total_packets) called for each sent packet.
        Returns True if all packets sent successfully, False otherwise.
        """
        total_packets = (len(data) + MAX_PAYLOAD_PER_PACKET - 1) // MAX_PAYLOAD_PER_PACKET
        
        for seq_num in range(total_packets):
            start_offset = seq_num * MAX_PAYLOAD_PER_PACKET
            end_offset = min(start_offset + MAX_PAYLOAD_PER_PACKET, len(data))
            payload = data[start_offset:end_offset]
            
            packet = UDPPacket(seq_num, total_packets, payload)
            packet_bytes = packet.to_bytes()
            
            # Retry logic for packet send
            for attempt in range(MAX_RETRIES):
                try:
                    self.sock.sendto(packet_bytes, (self.host, self.port))
                    break
                except socket.timeout:
                    if attempt == MAX_RETRIES - 1:
                        return False
                    time.sleep(0.1)
            
            if progress_callback:
                progress_callback(seq_num + 1, total_packets)
        
        return True

    def close(self):
        try:
            self.sock.close()
        except:
            pass


class UDPReceiver:
    """Receives data over UDP in sequenced packets."""
    
    def __init__(self, host: str, port: int, timeout: float = UDP_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self.sock.bind((host, port))
        
        self.received_packets = {}  # seq_num -> bytes
        self.total_packets = None
        self.completed = False

    def receive_data(self, progress_callback: Optional[Callable[[int, int], None]] = None) -> Optional[bytes]:
        """
        Receive data as a sequence of UDP packets.
        Returns complete data when all packets received, None if timed out.
        progress_callback(received_count, total_packets) called on each packet.
        """
        while not self.completed:
            try:
                packet_bytes, addr = self.sock.recvfrom(PACKET_HEADER_SIZE + MAX_PAYLOAD_PER_PACKET)
                packet = UDPPacket.from_bytes(packet_bytes)
                
                if packet is None:
                    continue
                
                self.total_packets = packet.total_packets
                self.received_packets[packet.seq_num] = packet.payload
                
                if progress_callback:
                    progress_callback(len(self.received_packets), self.total_packets)
                
                # Check if we have all packets
                if len(self.received_packets) == self.total_packets:
                    self.completed = True
                    break
                    
            except socket.timeout:
                if self.received_packets:
                    # Partial data received before timeout
                    break
                return None
        
        if not self.received_packets:
            return None
        
        # Reassemble data in order
        result = b""
        for seq_num in sorted(self.received_packets.keys()):
            result += self.received_packets[seq_num]
        
        return result

    def get_missing_packets(self) -> list[int]:
        """Return list of packet sequence numbers that haven't been received yet."""
        if self.total_packets is None:
            return []
        return [i for i in range(self.total_packets) if i not in self.received_packets]

    def close(self):
        try:
            self.sock.close()
        except:
            pass


class ResumableUDPTransfer:
    """
    Handles resumable UDP transfers by tracking packet state.
    Client can reconnect and resume from last received packet.
    """
    
    def __init__(self):
        self.state = {}  # transfer_id -> {total_packets, received_packets, data_chunks}

    def start_transfer(self, transfer_id: str, total_packets: int):
        """Initiate a new transfer."""
        self.state[transfer_id] = {
            "total_packets": total_packets,
            "received_packets": set(),
            "data_chunks": {},
        }

    def add_packet(self, transfer_id: str, seq_num: int, data: bytes):
        """Add a received packet to the transfer."""
        if transfer_id not in self.state:
            return False
        
        state = self.state[transfer_id]
        state["received_packets"].add(seq_num)
        state["data_chunks"][seq_num] = data
        
        return len(state["received_packets"]) == state["total_packets"]

    def is_complete(self, transfer_id: str) -> bool:
        """Check if transfer is complete."""
        if transfer_id not in self.state:
            return False
        state = self.state[transfer_id]
        return len(state["received_packets"]) == state["total_packets"]

    def get_missing_packets(self, transfer_id: str) -> list[int]:
        """Get list of missing packet numbers."""
        if transfer_id not in self.state:
            return []
        state = self.state[transfer_id]
        return sorted([i for i in range(state["total_packets"]) if i not in state["received_packets"]])

    def get_data(self, transfer_id: str) -> Optional[bytes]:
        """Retrieve complete data if transfer is done."""
        if not self.is_complete(transfer_id):
            return None
        
        state = self.state[transfer_id]
        result = b""
        for seq_num in range(state["total_packets"]):
            result += state["data_chunks"].get(seq_num, b"")
        
        # Cleanup
        del self.state[transfer_id]
        return result
