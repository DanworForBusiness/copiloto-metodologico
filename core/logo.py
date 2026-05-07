import streamlit as st
import streamlit.components.v1 as components

# Leer el archivo HTML del logo animado
with open("assets/danwor-animated-logo.html", "r", encoding="utf-8") as f:
    logo_html = f.read()

components.html(logo_html, height=200, scrolling=False)