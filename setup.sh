#!/bin/bash

# setup.sh - Setup script for Buffet-Safety Project
# Author: TransformsAI
# Description: Installs prerequisites, creates virtual environment, and configures the project

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get current directory and user
CURRENT_DIR=$(pwd)
CURRENT_USER=$(whoami)
PROJECT_NAME=$(basename "$CURRENT_DIR")

echo -e "${BLUE}===========================================${NC}"
echo -e "${BLUE}  Buffet Safety Setup Script     ${NC}"
echo -e "${BLUE}===========================================${NC}"
echo -e "${YELLOW}Current Directory: ${CURRENT_DIR}${NC}"
echo -e "${YELLOW}Current User: ${CURRENT_USER}${NC}"
echo -e "${YELLOW}Project: ${PROJECT_NAME}${NC}"
echo ""

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to install system prerequisites
install_prerequisites() {
    echo -e "${BLUE}Installing system prerequisites...${NC}"
    
    # Update package list
    sudo apt update
    
    # Install cmake if not present
    if ! command_exists cmake; then
        echo -e "${YELLOW}Installing cmake...${NC}"
        sudo apt install -y cmake
    else
        echo -e "${GREEN}cmake is already installed${NC}"
    fi
    
    # Install python3-venv if not present
    if ! dpkg -l | grep -q python3-venv; then
        echo -e "${YELLOW}Installing python3-venv...${NC}"
        sudo apt install -y python3-venv
    else
        echo -e "${GREEN}python3-venv is already installed${NC}"
    fi
    
    # Install python3-dev for compilation
    if ! dpkg -l | grep -q python3-dev; then
        echo -e "${YELLOW}Installing python3-dev...${NC}"
        sudo apt install -y python3-dev
    else
        echo -e "${GREEN}python3-dev is already installed${NC}"
    fi
    
    # Install MQTT prerequisites (from README)
    echo -e "${YELLOW}Setting up MQTT (Mosquitto)...${NC}"
    if ! command_exists mosquitto; then
        sudo apt-add-repository -y ppa:mosquitto-dev/mosquitto-ppa
        sudo apt install -y mosquitto
        
        # Configure mosquitto
        sudo tee /etc/mosquitto/mosquitto.conf > /dev/null << EOF
listener 1883
protocol mqtt

listener 9001
protocol websockets

allow_anonymous true
EOF
        sudo systemctl restart mosquitto
        sudo systemctl enable mosquitto
        echo -e "${GREEN}Mosquitto MQTT broker installed and configured${NC}"
    else
        echo -e "${GREEN}Mosquitto is already installed${NC}"
    fi
}

# Function to create and setup virtual environment
setup_venv() {
    echo -e "${BLUE}Setting up Python virtual environment...${NC}"
    
    # Remove existing venv if it exists
    if [ -d "venv" ]; then
        echo -e "${YELLOW}Removing existing virtual environment...${NC}"
        rm -rf venv
    fi
    
    # Create new virtual environment
    echo -e "${YELLOW}Creating new virtual environment...${NC}"
    python3 -m venv venv
    
    # Activate virtual environment
    source venv/bin/activate
    
    # Upgrade pip
    echo -e "${YELLOW}Upgrading pip...${NC}"
    pip install -U pip
    
    # Install requirements
    if [ -f "requirements.txt" ]; then
        echo -e "${YELLOW}Installing Python requirements...${NC}"
        pip install -r requirements.txt
        echo -e "${GREEN}Requirements installed successfully${NC}"
    else
        echo -e "${RED}requirements.txt not found!${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}Virtual environment setup complete${NC}"
}

# Function to update service file
update_service_file() {
    echo -e "${BLUE}Updating service file...${NC}"
    
    SERVICE_FILE=""
    # Look for service files
    for file in *.service; do
        if [ -f "$file" ]; then
            SERVICE_FILE="$file"
            break
        fi
    done
    
    if [ -z "$SERVICE_FILE" ]; then
        echo -e "${YELLOW}No service file found, skipping service update${NC}"
        return
    fi
    
    echo -e "${YELLOW}Found service file: ${SERVICE_FILE}${NC}"
    
    # Create backup of original service file
    cp "$SERVICE_FILE" "${SERVICE_FILE}.backup"
    
    # Update User and WorkingDirectory in service file
    sed -i "s|^User=.*|User=$CURRENT_USER|g" "$SERVICE_FILE"
    sed -i "s|^WorkingDirectory=.*|WorkingDirectory=$CURRENT_DIR|g" "$SERVICE_FILE"
    
    # Fix the ExecStart line to prevent service restart loops
    # Replace problematic nohup commands with proper systemd service execution
    if grep -q "nohup.*&" "$SERVICE_FILE"; then
        echo -e "${YELLOW}Fixing service ExecStart configuration...${NC}"
        
        # Extract the Python script name from the current ExecStart line
        PYTHON_SCRIPT=$(grep "ExecStart=" "$SERVICE_FILE" | sed -n 's/.*python \([^ ]*\).*/\1/p')
        CONFIG_FILE=$(grep "ExecStart=" "$SERVICE_FILE" | sed -n 's/.*python [^ ]* \([^ ]*\).*/\1/p')
        
        if [ -z "$PYTHON_SCRIPT" ]; then
            PYTHON_SCRIPT="buffet.py"  # Default fallback
        fi
        if [ -z "$CONFIG_FILE" ]; then
            CONFIG_FILE="config.json"  # Default fallback
        fi
        
        # Update ExecStart to use proper systemd service format
        sed -i "s|^ExecStart=.*|ExecStart=$CURRENT_DIR/venv/bin/python $PYTHON_SCRIPT $CONFIG_FILE|g" "$SERVICE_FILE"
        
        echo -e "${GREEN}Fixed ExecStart configuration to prevent restart loops${NC}"
        echo -e "${YELLOW}  Script: ${PYTHON_SCRIPT}${NC}"
        echo -e "${YELLOW}  Config: ${CONFIG_FILE}${NC}"
    fi
    
    echo -e "${GREEN}Service file updated:${NC}"
    echo -e "${YELLOW}  User: ${CURRENT_USER}${NC}"
    echo -e "${YELLOW}  WorkingDirectory: ${CURRENT_DIR}${NC}"
}

# Function to create systemd service (optional)
install_service() {
    SERVICE_FILE=""
    for file in *.service; do
        if [ -f "$file" ]; then
            SERVICE_FILE="$file"
            break
        fi
    done
    
    if [ -z "$SERVICE_FILE" ]; then
        echo -e "${YELLOW}No service file found, skipping systemd installation${NC}"
        return
    fi
    
    read -p "Do you want to install the systemd service? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Installing systemd service...${NC}"
        sudo cp "$SERVICE_FILE" "/etc/systemd/system/"
        sudo systemctl daemon-reload
        sudo systemctl enable "$(basename $SERVICE_FILE)"
        echo -e "${GREEN}Service installed and enabled${NC}"
        echo -e "${YELLOW}You can start it with: sudo systemctl start $(basename $SERVICE_FILE .service)${NC}"
    fi
}

# Main execution
echo -e "${GREEN}Starting setup process...${NC}"

# Check if we're in the right directory
if [ ! -f "requirements.txt" ] || [ ! -f "README.md" ]; then
    echo -e "${RED}Error: This doesn't appear to be a valid project directory${NC}"
    echo -e "${RED}Make sure you're in the project root with requirements.txt and README.md${NC}"
    exit 1
fi

# Install prerequisites
install_prerequisites

# Setup virtual environment
setup_venv

# Update service file
update_service_file

# Optionally install systemd service
install_service

echo ""
echo -e "${GREEN}===========================================${NC}"
echo -e "${GREEN}  Setup completed successfully!           ${NC}"
echo -e "${GREEN}===========================================${NC}"
echo -e "${YELLOW}Next steps:${NC}"
echo -e "${YELLOW}1. Run ./configure.py to configure your project${NC}"
echo -e "${YELLOW}2. Run ./run-project.sh to start the application${NC}"
echo -e "${YELLOW}3. Or activate venv manually: source venv/bin/activate${NC}"
echo ""
echo -e "${BLUE}Virtual environment created at: ${CURRENT_DIR}/venv${NC}"
echo -e "${BLUE}To activate: source venv/bin/activate${NC}"
echo ""
