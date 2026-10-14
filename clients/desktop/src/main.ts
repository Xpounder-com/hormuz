import { mount } from 'svelte';
import App from './App.svelte';
import './styles.css';
import './workflows.css';

mount(App, { target: document.getElementById('app')! });
