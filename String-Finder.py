# ================ Find String in Large Files ===============

# This script searches a specified file with a given string or word to look for matches and return results.
# Useful for searching text files eg. serach rockyou and other password lists for matching password phrase. 


import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# Function to estimate memory usage of an object
def get_size(obj):
    """Return the approximate memory footprint of an object and all of its contents."""
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        size += sum(get_size(v) for v in obj.values())
    elif isinstance(obj, (list, tuple, set)):
        size += sum(get_size(v) for v in obj)
    return size

def read_file_chunks(file_path, chunk_size=1024*1024):
    """Generator to read a file in chunks."""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            yield chunk

def search_in_chunk(chunk, search_string):
    """Search for the string in a chunk and return the count of occurrences."""
    return chunk.count(search_string)

def search_in_file(file_path, search_string, save_to_file=False, chunk_size=1024*1024, max_memory_usage=2 * 1024 * 1024 * 1024):
    """Search for a string in a file using multithreading and show progress."""
    total_size = os.path.getsize(file_path)
    total_occurrences = 0
    lock = threading.Lock()
    results = []  # List to store chunk contents for saving to file

    def process_chunks(chunks):
        nonlocal total_occurrences
        futures = []
        for chunk in chunks:
            future = executor.submit(search_in_chunk, chunk, search_string)
            future.chunk = chunk  # Attach chunk to future object for reference
            futures.append(future)
        for future in as_completed(futures):
            occurrences = future.result()
            with lock:
                total_occurrences += occurrences
                pbar.set_description(f"Searching (Found: {total_occurrences})")
                pbar.update(chunk_size)
                if save_to_file and occurrences > 0:
                    results.append(future.chunk)  # Store chunk content for saving to file

    with ThreadPoolExecutor(max_workers=1) as executor, \
         tqdm(total=total_size, unit='B', unit_scale=True, desc='Searching') as pbar:
        chunks = []
        memory_usage = 0
        for chunk in read_file_chunks(file_path, chunk_size=chunk_size):
            chunk_size = get_size(chunk)
            if memory_usage + chunk_size > max_memory_usage:
                process_chunks(chunks)
                chunks = []
                memory_usage = 0
            chunks.append(chunk)
            memory_usage += chunk_size
        
        if chunks:
            process_chunks(chunks)

    if save_to_file and results:
        with open('found.log', 'w', encoding='utf-8') as f:
            for result in results:
                f.write(result + '\n')  # Write each chunk content to found.log

    return total_occurrences

if __name__ == "__main__":
    file_path = input("Enter the path to the text file: ")
    search_string = input("Enter the string to search for: ")
    save_to_file_input = input("Would you like to save all matches to 'found.log' file? (yes/no): ").strip().lower()

    save_to_file = save_to_file_input == 'yes'

    # Limit RAM consumption to approximately 2GB (adjust as needed)
    max_memory_usage = 2 * 1024 * 1024 * 1024  # 2 GB

    occurrences = search_in_file(file_path, search_string, save_to_file=save_to_file, max_memory_usage=max_memory_usage)
    print(f"Total occurrences of '{search_string}': {occurrences}")

    if save_to_file:
        print("All matches have been saved to 'found.log'")