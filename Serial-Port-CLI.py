import serial
import serial.tools.list_ports
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

def list_com_ports():
    """Scan for available COM ports."""
    ports = serial.tools.list_ports.comports()
    return [port.device for port in ports]

def read_from_serial(ser, output_box):
    """Read data from the serial port and display in the output box."""
    buffer = ""  # Buffer to accumulate characters
    try:
        while ser.is_open:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting).decode('utf-8')
                for char in data:
                    if char == '\n':  # Newline detected
                        output_box.insert(tk.END, buffer + '\n')
                        output_box.see(tk.END)  # Auto-scroll to the latest line
                        buffer = ""  # Reset the buffer
                    else:
                        buffer += char  # Accumulate characters in the buffer
    except serial.SerialException as e:
        output_box.insert(tk.END, f"Error reading from serial port: {e}\n")

def send_command(ser, command_entry, output_box):
    """Send command to the serial port."""
    command = command_entry.get()
    if command.strip():
        try:
            ser.write((command + '\n').encode('utf-8'))  # Send command followed by newline
            output_box.insert(tk.END, f"Sent: {command}\n")
            output_box.see(tk.END)  # Auto-scroll to the latest line
        except serial.SerialException as e:
            output_box.insert(tk.END, f"Error writing to serial port: {e}\n")
        command_entry.delete(0, tk.END)  # Clear the entry box

def toggle_dark_mode(root, widgets, dark_mode_var):
    """Toggle dark mode on and off."""
    if dark_mode_var.get():
        root.configure(bg="#2e2e2e")
        for widget in widgets:
            widget.configure(bg="#2e2e2e", fg="#ffffff", insertbackground="#ffffff")
    else:
        root.configure(bg="#f0f0f0")
        for widget in widgets:
            widget.configure(bg="#ffffff", fg="#000000", insertbackground="#000000")

def create_gui():
    """Create a GUI for the serial communication."""
    ser = None  # Serial object

    def connect():
        nonlocal ser
        selected_port = port_var.get()
        selected_baud_rate = int(baud_var.get())
        try:
            ser = serial.Serial(selected_port, selected_baud_rate, timeout=1)
            output_box.insert(tk.END, f"Connected to {selected_port} at {selected_baud_rate} baud\n")
            output_box.see(tk.END)

            # Start a thread to read from the serial port
            read_thread = threading.Thread(target=read_from_serial, args=(ser, output_box), daemon=True)
            read_thread.start()

            connect_button.configure(state=tk.DISABLED)
            disconnect_button.configure(state=tk.NORMAL)
        except serial.SerialException as e:
            output_box.insert(tk.END, f"Failed to connect: {e}\n")
            output_box.see(tk.END)

    def disconnect():
        nonlocal ser
        if ser and ser.is_open:
            ser.close()
            output_box.insert(tk.END, "Disconnected\n")
            output_box.see(tk.END)
        connect_button.configure(state=tk.NORMAL)
        disconnect_button.configure(state=tk.DISABLED)

    root = tk.Tk()
    root.title("Serial Port Communication")
    root.geometry("900x620")

    # Top frame for controls
    top_frame = tk.Frame(root)
    top_frame.pack(fill=tk.X, padx=10, pady=10)

    # Widgets for port and baud rate selection
    port_label = tk.Label(top_frame, text="Port:")
    port_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
    available_ports = list_com_ports()
    port_var = tk.StringVar(value=available_ports[0] if available_ports else "")
    port_dropdown = ttk.Combobox(top_frame, textvariable=port_var, values=available_ports, state="readonly", width=15)
    port_dropdown.grid(row=0, column=1, padx=5, pady=5, sticky="w")

    baud_label = tk.Label(top_frame, text="Baud Rate:")
    baud_label.grid(row=0, column=2, padx=5, pady=5, sticky="w")
    common_baud_rates = [9600, 19200, 38400, 57600, 115200]
    baud_var = tk.StringVar(value=str(common_baud_rates[0]))
    baud_dropdown = ttk.Combobox(top_frame, textvariable=baud_var, values=common_baud_rates, state="readonly", width=10)
    baud_dropdown.grid(row=0, column=3, padx=5, pady=5, sticky="w")

    # Connect and Disconnect buttons
    connect_button = tk.Button(top_frame, text="Connect", command=connect)
    connect_button.grid(row=0, column=4, padx=5, pady=5, sticky="w")

    disconnect_button = tk.Button(top_frame, text="Disconnect", command=disconnect, state=tk.DISABLED)
    disconnect_button.grid(row=0, column=5, padx=5, pady=5, sticky="w")

    # Dark mode toggle
    dark_mode_var = tk.BooleanVar()
    dark_mode_toggle = tk.Checkbutton(top_frame, text="Dark Mode", variable=dark_mode_var, command=lambda: toggle_dark_mode(root, [output_box, command_entry], dark_mode_var))
    dark_mode_toggle.grid(row=0, column=6, padx=5, pady=5, sticky="w")

    # Output box
    output_box = scrolledtext.ScrolledText(root, wrap=tk.WORD, height=30, width=100)
    output_box.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

    # Bottom frame for command input
    bottom_frame = tk.Frame(root)
    bottom_frame.pack(fill=tk.X, padx=5, pady=5)

    # Command entry box
    command_entry = tk.Entry(bottom_frame, width=80)
    command_entry.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.X, expand=True)

    # Send button
    send_button = tk.Button(bottom_frame, text="Send", command=lambda: send_command(ser, command_entry, output_box))
    send_button.pack(side=tk.LEFT, padx=5, pady=5)

    # Bind Enter key to send command
    root.bind('<Return>', lambda event: send_command(ser, command_entry, output_box))

    root.mainloop()

# Create the GUI
create_gui()
