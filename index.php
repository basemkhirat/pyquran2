<?php
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
echo json_encode([
    'name' => 'Quran Voice Recognition API',
    'status' => 'ok',
    'socket_io_path' => '/socket.io'
]);
