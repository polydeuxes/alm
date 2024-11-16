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
            
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_data else None,
            text=True,
            shell=is_media_cmd  # Use shell=True for ffmpeg/ffprobe commands
        )
        
        stdout, stderr = process.communicate(input=input_data, timeout=timeout)
        
        # For media commands, combine stdout and stderr for parsing
        if is_media_cmd:
            output = stdout or stderr
            success = process.returncode == 0 and (
                ('ffprobe' in command and output and 'tags' in output) or
                ('ffmpeg' in command and stderr and 'Stream mapping' in stderr)
            )
            if not success:
                config.logger.debug(f"Media command failed:\nstdout: {stdout}\nstderr: {stderr}")
        else:
            success = process.returncode == 0
            output = stdout
            
            if stdout:
                config.logger.debug(f"Command stdout: {stdout[:200]}..." if len(stdout) > 200 else stdout)
            if stderr:
                config.logger.debug(f"Command stderr: {stderr}")
            
            if not success:
                config.logger.error(f"Command failed with code {process.returncode}")
        
        return {
            'success': success,
            'output': output.strip() if output else '',
            'error': stderr.strip() if stderr else None,
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
