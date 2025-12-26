import os
import sys
import time
import select
import subprocess

def get_active_ids(interface="can0", duration=60):
    """
    Listens to the CAN bus for a specified duration and returns a set of unique IDs.
    Uses 'candump' as a subprocess to capture traffic.
    """
    print(f"Scanning {interface} for {duration} seconds...")
    print("Please ensure the bus is active (engine running or ignition on).")
    
    # Start candump process
    # Output format: <interface> <id>   [<len>] <data>
    # Example: can0 123   [8] 11 22 33 44 55 66 77 88
    process = subprocess.Popen(
        ["candump", "-L", interface], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        universal_newlines=True
    )

    start_time = time.time()
    unique_ids = set()
    total_frames = 0

    try:
        while (time.time() - start_time) < duration:
            # Check if process is still running
            if process.poll() is not None:
                break
                
            # Non-blocking read
            reads = [process.stdout.fileno()]
            ret = select.select(reads, [], [], 1.0)
            
            if ret[0]:
                line = process.stdout.readline()
                if line:
                    total_frames += 1
                    try:
                        # Log file format (-L): (1622019432.123456) can0 123#112233
                        parts = line.split()
                        if len(parts) >= 3:
                            can_frame = parts[2] # 123#112233
                            can_id_hex = can_frame.split('#')[0]
                            can_id = int(can_id_hex, 16)
                            unique_ids.add(can_id)
                            
                            # Simple progress indicator
                            if total_frames % 50 == 0:
                                sys.stdout.write(f"\rFrames: {total_frames} | Unique IDs: {len(unique_ids)}")
                                sys.stdout.flush()
                    except (ValueError, IndexError):
                        continue
                        
    except KeyboardInterrupt:
        print("\nScan interrupted by user.")
    finally:
        process.terminate()
        
    print(f"\n\nScan Complete.")
    print(f"Total Frames: {total_frames}")
    print(f"Unique IDs Found: {len(unique_ids)}")
    print("-" * 40)
    
    sorted_ids = sorted(list(unique_ids))
    for aid in sorted_ids:
        print(f"ID: 0x{aid:03X} ({aid})")
        
    print("-" * 40)
    return sorted_ids

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Warning: You might need root privileges to access CAN interface directly, depending on permissions.")
        
    duration = 60
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except ValueError:
            print("Usage: python3 find_active_ids.py [duration_seconds]")
            sys.exit(1)
            
    get_active_ids(duration=duration)
