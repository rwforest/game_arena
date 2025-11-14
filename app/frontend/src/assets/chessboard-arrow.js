'use strict';
// TODO: wrap all in an anonymous function
var ChessboardArrow = function (boardElOrId, configs = {}) {
    // default const
    var ArrowWidth = 15;
    var HeadLength = 50;
    var FillColor = 'rgba(0, 0, 240, .6)';
    var lastArrow;

    var $container = checkContainerArg(boardElOrId);
    if (!$container) return null;

    var cfg = {
        width: configs.width || ArrowWidth,
        fillColor: configs.fillColor || FillColor,
        headLength: configs.headLength || HeadLength
    };
    var canvas = document.createElement('canvas'); // todo: create and make id dynamic
    var ctx = canvas.getContext('2d');
    createCanvas();

    function drawArrow(from, to, options = {}) {
        if (!isNaN(options.duration) && options.duration > 0) {
            setTimeout(() => {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                lastArrow && drawArrow(
                    lastArrow.from,
                    lastArrow.to,
                    lastArrow.options
                );
            }, options.duration);
        } else {
            lastArrow = {
                from: from,
                to: to,
                options: options
            }
        }

        var $from = $container.find('.square-' + from);
        var $to = $container.find('.square-' + to);
        var canvasPos = canvas.getBoundingClientRect(); // move it to createCanvas

        var sq1 = $from[0].getBoundingClientRect();
        var sq2 = $to[0].getBoundingClientRect();
        createArrow(
            sq1.left + sq1.width / 2 - canvasPos.left,
            sq1.top + sq1.height / 2 - canvasPos.top,
            sq2.left + sq2.width / 2 - canvasPos.left,
            sq2.top + sq2.height / 2 - canvasPos.top,
            options
        );
    }

    function clear() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    function createCanvas() {
        // canvas.id = "myCanvas";
        canvas.width = $container.width();
        canvas.height = $container.height();

        // set styles
        canvas.style.position = 'absolute';
        canvas.style.top = '0';
        canvas.style.left = '0';
        canvas.style.zIndex = '100';
        canvas.style.pointerEvents = 'none';

        // append to board container
        $container.append(canvas);
    }

    function createArrow(x1, y1, x2, y2, options) {
        if (options.clear != false) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }

        options = options || {};
        options.width = cfg.width || ArrowWidth;
        options.fillColor = options.fillColor || cfg.fillColor || FillColor;
        options.headLength = options.headLength || cfg.headLength || HeadLength;
        if (options.headLength < options.width + 1) {
            options.headLength = options.width + 1;
        }
        options.head_angle = options.head_angle || Math.PI / 6;
        var angle = Math.atan2(y2 - y1, x2 - x1);
        var angNeg = angle - options.head_angle;
        var angPos = angle + options.head_angle;
        var tri_point1 = {
            x: x2 - options.headLength * Math.cos(angNeg),
            y: y2 - options.headLength * Math.sin(angNeg)
        };
        var tri_point2 = {
            x: x2 - options.headLength * Math.cos(angPos),
            y: y2 - options.headLength * Math.sin(angPos)
        };
        /// Since the line has a width, we need to create a new line by moving the point half of the width and then rotating it to match the line.
        var p1 = rotate_point(x1, y1 + options.width / 2, x1, y1, angle);
        var p2 = rotate_point(x2, y2 + options.width / 2, x2, y2, angle);
        /// Find the point at which the line will reach the bottom of the triangle.
        var int2 = get_intersect(p1.x, p1.y, p2.x, p2.y, tri_point1.x, tri_point1.y, tri_point2.x, tri_point2.y);
        var p3 = rotate_point(x1, y1 - options.width / 2, x1, y1, angle);
        var p4 = rotate_point(x2, y2 - options.width / 2, x2, y2, angle);
        var int3 = get_intersect(p3.x, p3.y, p4.x, p4.y, tri_point1.x, tri_point1.y, tri_point2.x, tri_point2.y);
        ctx.fillStyle = options.fillColor;
        ctx.beginPath();
        ctx.arc(x1, y1, options.width / 2, angle - Math.PI / 2, angle - Math.PI * 1.5, true);
        ctx.lineTo(int2.x, int2.y);
        ctx.lineTo(tri_point1.x, tri_point1.y);
        ctx.lineTo(x2, y2);
        ctx.lineTo(tri_point2.x, tri_point2.y);
        ctx.lineTo(int3.x, int3.y);
        ctx.closePath();
        // if (options.lineWidth) {
        //     ctx.lineWidth = options.lineWidth;
        //     ctx.strokeStyle = options.strokeStyle;
        //     ctx.stroke();
        // }

        ctx.fill();
    }

    return {
        drawArrow: drawArrow,
        clear: clear
    };
};

function get_intersect(x1, y1, x2, y2, x3, y3, x4, y4) {
    /// See https://en.wikipedia.org/wiki/Line–line_intersection.
    return {
        x: ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / ((x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)),
        y: ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / ((x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4))
    };
}

function rotate_point(point_x, point_y, origin_x, origin_y, angle) {
    return {
        x: Math.cos(angle) * (point_x - origin_x) - Math.sin(angle) * (point_y - origin_y) + origin_x,
        y: Math.sin(angle) * (point_x - origin_x) + Math.cos(angle) * (point_y - origin_y) + origin_y
    };
}

function checkContainerArg(containerElOrString) {
    // convert containerEl to query selector if it is a string
    if (typeof (containerElOrString) === 'string' &&
        containerElOrString.charAt(0) !== '#') {
        containerElOrString = '#' + containerElOrString;
    }

    // containerEl must be something that becomes a jQuery collection of size 1
    var $container = $(containerElOrString);
    if ($container.length !== 1) {
        var errorMsg2 =
            'The first argument to ChessboardArrow() must be the ID of a DOM node, ' +
            'an ID query selector, or a single DOM node.' +
            '\n\n' +
            'Exiting';
        window.alert(errorMsg2);
        return false;
    }

    return $container.find('.chessboard-63f37');
}
