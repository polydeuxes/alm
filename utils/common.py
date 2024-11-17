# utils/common.py
import subprocess
import config

def run_command(command, input_data=None, timeout=300):
    """Execute a shell command with improved logging and error handling"""
    try:
        # Handle ffmpeg/ffprobe commands differently
        is_media_cmd = isinstance(command, str) and ('ffmpeg' in command or 'ffprobe' in command)
        # Split command if it's a string and not a media command
        if isinstance(command, str) and not is_media_cmd:
            command = command.split()

        # Create process with line buffering
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_data else None,
            text=True,
            bufsize=1,
            shell=is_media_cmd
        )

        output_lines = []
        error_lines = []

        # Read output in real-time
        while True:
            # Check stdout
            stdout_line = process.stdout.readline()
            if stdout_line:
                line = stdout_line.strip()
                output_lines.append(line)
                config.logger.debug(f"Command stdout: {line}")

            # Check stderr
            stderr_line = process.stderr.readline()
            if stderr_line:
                line = stderr_line.strip()
                error_lines.append(line)
                config.logger.debug(f"Command stderr: {line}")

            # Check if process has finished
            if process.poll() is not None:
                # Read any remaining output
                for line in process.stdout:
                    output_lines.append(line.strip())
                for line in process.stderr:
                    error_lines.append(line.strip())
                break

            # Break if both pipes are empty and process has ended
            if not stdout_line and not stderr_line and process.poll() is not None:
                break

        stdout = '\n'.join(output_lines)
        stderr = '\n'.join(error_lines)

        success = process.returncode == 0
        if not success:
            config.logger.error(f"Command failed with code {process.returncode}")
            config.logger.error(f"Error output: {stderr}")

        return {
            'success': success,
            'output': stdout,
            'error': stderr,
            'code': process.returncode
        }
    except Exception as e:
        cmd_str = ' '.join(command) if isinstance(command, list) else command
        config.logger.error(f"Command failed: {cmd_str}", exc_info=True)
        return {
            'success': False,
            'output': '',
            'error': str(e),
            'code': -1
        }
