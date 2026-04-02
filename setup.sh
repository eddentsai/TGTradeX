#!/bin/bash

# 創建虛擬環境
echo "創建虛擬環境..."
python3 -m venv venv

# 激活虛擬環境
echo "激活虛擬環境..."
source venv/bin/activate

# 安裝所需的套件
echo "安裝所需的套件..."
pip install -r requirements.txt

echo "安裝完成！"
echo "要激活虛擬環境，請運行: source venv/bin/activate"
